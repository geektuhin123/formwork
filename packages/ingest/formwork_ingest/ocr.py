"""OCR + geometric layout for scanned pages and images.

`OcrEngine` returns words with boxes and confidences; `layout_blocks` groups them into table and text blocks from
geometry alone (rows by baseline, cells by horizontal gaps). The engine is swappable: Tesseract is the MVP default
(ADR 0002); a Docling/PaddleOCR engine only has to return `Word`s.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np
import pytesseract as _pytesseract
from formwork_core.models import BlockType
from PIL import Image

from formwork_ingest.base import RawBlock, markdown_table

pytesseract: Any = _pytesseract  # untyped library


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float  # pixels
    conf: float  # 0..1


class OcrEngine(Protocol):
    name: str

    def words(self, image: Image.Image) -> list[Word]: ...


class TesseractOcr:
    name = "tesseract"

    def __init__(self, lang: str = "eng", psm: int = 6) -> None:
        self.lang = lang
        self.psm = psm

    def words(self, image: Image.Image) -> list[Word]:
        data = pytesseract.image_to_data(
            image, lang=self.lang, config=f"--psm {self.psm}", output_type=pytesseract.Output.DICT
        )
        out = []
        for i, txt in enumerate(data["text"]):
            conf = float(data["conf"][i])
            txt = txt.strip()
            if not txt or conf < 0 or set(txt) <= set("|_-—=~.:'\"`,"):
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            out.append(Word(txt, x, y, x + w, y + h, conf / 100.0))
        return out


def default_engine() -> OcrEngine:
    return TesseractOcr()


def deskew(image: Image.Image, max_angle: float = 12.0) -> tuple[Image.Image, float]:
    """Estimate small skew from the dominant text-line angle (Hough) and rotate it away."""
    gray = np.array(image.convert("L"))
    scale = 1000 / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    smeared = cv2.dilate(binary, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1)))
    lines = cv2.HoughLinesP(
        smeared, 1, np.pi / 720, threshold=120, minLineLength=small.shape[1] // 5, maxLineGap=12
    )
    if lines is None:
        return image, 0.0
    angles = []
    for x0, y0, x1, y1 in lines.reshape(-1, 4):
        a = float(np.degrees(np.arctan2(y1 - y0, x1 - x0)))
        if abs(a) <= max_angle:
            angles.append(a)
    if len(angles) < 5:
        return image, 0.0
    angle = float(statistics.median(angles))
    if abs(angle) < 0.15:
        return image, 0.0
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor="white"), angle


def fix_rotation(image: Image.Image) -> tuple[Image.Image, int]:
    """Correct 90/180/270 degree rotation using Tesseract OSD when it is confident."""
    try:
        osd = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
    except Exception:  # OSD needs enough text and osd.traineddata; absence is not an error
        return image, 0
    rot = int(osd.get("rotate", 0))
    if rot in (90, 180, 270) and float(osd.get("orientation_conf", 0)) >= 2.0:
        return image.rotate(-rot, expand=True, fillcolor="white"), rot
    return image, 0


@dataclass
class _Seg:
    words: list[Word]

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def x1(self) -> float:
        return self.words[-1].x1

    @property
    def y0(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def y1(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def _lines(words: list[Word]) -> list[list[Word]]:
    """Group words into text lines left to right, following each line's own baseline so residual skew cannot split it."""
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.x0):
        cy, h = (w.y0 + w.y1) / 2, w.y1 - w.y0
        best, best_d = None, 1e9
        for ln in lines:
            last = ln[-1]
            d = abs(cy - (last.y0 + last.y1) / 2)
            if d <= 0.5 * max(h, last.y1 - last.y0) and d < best_d:
                best, best_d = ln, d
        if best is None:
            lines.append([w])
        else:
            best.append(w)
    return sorted(lines, key=lambda ln: statistics.fmean((x.y0 + x.y1) / 2 for x in ln))


def _segments(line: list[Word], gap: float) -> list[_Seg]:
    segs = [_Seg([line[0]])]
    for w in line[1:]:
        if w.x0 - segs[-1].x1 > gap:
            segs.append(_Seg([w]))
        else:
            segs[-1].words.append(w)
    return segs


def _assign(segs: list[_Seg], cols: list[tuple[float, float]]) -> list[int] | None:
    """Map each segment to a distinct header column by horizontal overlap/nearest centre."""
    idx = []
    for s in segs:
        c = (s.x0 + s.x1) / 2
        best = min(
            range(len(cols)),
            key=lambda i: (
                0 if cols[i][0] <= c <= cols[i][1] else min(abs(c - cols[i][0]), abs(c - cols[i][1]))
            ),
        )
        idx.append(best)
    return idx if len(set(idx)) == len(idx) and idx == sorted(idx) else None


def layout_blocks(words: list[Word], width: int, height: int, page: int) -> list[RawBlock]:
    if not words:
        return []
    char_h = statistics.median(w.y1 - w.y0 for w in words)
    lines = [_segments(ln, gap=1.6 * char_h) for ln in _lines(words)]
    out: list[RawBlock] = []

    def box(segs: list[_Seg]) -> tuple[float, float, float, float]:
        return (
            min(s.x0 for s in segs) / width,
            min(s.y0 for s in segs) / height,
            max(s.x1 for s in segs) / width,
            max(s.y1 for s in segs) / height,
        )

    def conf(segs: list[_Seg]) -> float:
        ws = [w for s in segs for w in s.words]
        return sum(w.conf * len(w.text) for w in ws) / max(1, sum(len(w.text) for w in ws))

    i = 0
    loose: list[_Seg] = []
    loose_lines: list[tuple[int, list[_Seg]]] = []
    while i < len(lines):
        head = lines[i]
        n = len(head)
        rows: list[tuple[list[_Seg], list[int]]] = [(head, list(range(n)))]
        if n >= 3:
            # Column extents grow as rows are added, so a left-aligned header and right-aligned numbers still agree.
            ext = [(s.x0, s.x1) for s in head]
            j = i + 1
            while j < len(lines):
                ln = lines[j]
                idx = (
                    list(range(n))
                    if len(ln) == n
                    else (_assign(ln, ext) if max(2, n - 1) <= len(ln) < n else None)
                )
                if idx is None:
                    break
                rows.append((ln, idx))
                for s, k in zip(ln, idx, strict=True):
                    ext[k] = (min(ext[k][0], s.x0), max(ext[k][1], s.x1))
                j += 1
        if len(rows) >= 2:
            # A header on a shaded band often loses a word to OCR: adopt the line just above if it fits the columns.
            if loose_lines and loose_lines[-1][0] == i - 1 and max(2, n - 2) <= len(loose_lines[-1][1]) < n:
                above = loose_lines[-1][1]
                idx_above = _assign(above, ext)
                if idx_above is not None:
                    rows.insert(0, (above, idx_above))
                    del loose[len(loose) - len(above) :]
                    loose_lines.pop()
            cells = []
            for ln, idx in rows:
                row = [""] * n
                for s, k in zip(ln, idx, strict=True):
                    row[k] = s.text
                cells.append(row)
            flat = [s for ln, _ in rows for s in ln]
            out.append(
                RawBlock(
                    BlockType.TABLE,
                    markdown_table(cells),
                    page,
                    box(flat),
                    cells=cells,
                    ocr_confidence=conf(flat),
                )
            )
            i += len(rows)
        else:
            loose.extend(head)
            loose_lines.append((i, head))
            i += 1

    # Group remaining fragments into paragraphs: same left edge (or overlapping), small vertical gap.
    groups: list[list[_Seg]] = []
    for s in sorted(loose, key=lambda s: (s.y0, s.x0)):
        for g in groups:
            last = g[-1]
            overlap = min(s.x1, max(x.x1 for x in g)) - max(s.x0, min(x.x0 for x in g))
            if 0 <= s.y0 - last.y1 <= 1.1 * char_h and (
                abs(s.x0 - last.x0) <= 2 * char_h or overlap > 0.5 * (s.x1 - s.x0)
            ):
                g.append(s)
                break
        else:
            groups.append([s])
    for g in groups:
        text = "\n".join(s.text for s in g)
        if (conf(g) < 0.35 and len(text) < 6) or (conf(g) < 0.6 and len(text) < 3):  # speckle read as text
            continue
        kv = sum(1 for s in g if ":" in s.text[:45]) >= max(1, len(g) // 2)
        out.append(
            RawBlock(
                BlockType.KEY_VALUE if kv else BlockType.TEXT, text, page, box(g), ocr_confidence=conf(g)
            )
        )
    return out


def remove_rules(image: Image.Image) -> Image.Image:
    """Erase long horizontal/vertical ruling lines (table grids, boxes); they wreck OCR of the cells they enclose."""
    gray = np.array(image.convert("L"))
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = binary.shape
    horiz = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 25), 1))
    )
    vert = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 40)))
    )
    rules = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8))
    if not rules.any():
        return image
    gray[rules > 0] = 255
    return Image.fromarray(gray)


def ocr_page(image: Image.Image, page: int, engine: OcrEngine | None = None) -> list[RawBlock]:
    engine = engine or default_engine()
    words = [w for w in engine.words(remove_rules(image)) if not (w.conf < 0.3 and len(w.text) <= 2)]
    words = [w for w in words if any(ch.isalnum() for ch in w.text) or w.conf >= 0.6]
    return layout_blocks(words, image.width, image.height, page)
