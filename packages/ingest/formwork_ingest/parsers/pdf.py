from __future__ import annotations

import io
import re
import statistics
from typing import Any, ClassVar

import pymupdf
from formwork_core.context import JobContext
from formwork_core.errors import UnsupportedFile
from formwork_core.models import BlockType, CanonicalDocument
from PIL import Image

from formwork_ingest.base import (
    MAX_PAGES,
    PDF,
    BaseParser,
    ParsedDocument,
    RawBlock,
    StoredFile,
    document_id,
    finalize,
    markdown_table,
)
from formwork_ingest.ocr import deskew, ocr_page

TEXT_LAYER_MIN_CHARS = 200  # average per page; below this the PDF is treated as a scan
SCAN_DPI = 200
PREVIEW_DPI = 110
_KV = re.compile(r"^[^:]{1,40}:\s*\S")


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


class PdfParser(BaseParser):
    mime_types: ClassVar[set[str]] = {PDF}

    def parse_sync(self, file: StoredFile, ctx: JobContext) -> ParsedDocument:
        try:
            pdf: Any = pymupdf.open(stream=file.data, filetype="pdf")
        except Exception as exc:
            raise UnsupportedFile(f"cannot open PDF: {exc}") from exc
        if pdf.needs_pass:
            raise UnsupportedFile("password-protected PDFs are not supported")
        if pdf.page_count > MAX_PAGES:
            raise UnsupportedFile(f"PDF has {pdf.page_count} pages; the limit is {MAX_PAGES}")
        doc_id = document_id(ctx, file.sha256)
        chars = sum(len(p.get_text("text").strip()) for p in pdf)
        digital = pdf.page_count > 0 and chars / pdf.page_count > TEXT_LAYER_MIN_CHARS
        raw: list[RawBlock] = []
        images: dict[int, bytes] = {}
        for n, page in enumerate(pdf, start=1):
            if digital:
                raw.extend(self._text_page(page, n))
                images[n] = page.get_pixmap(dpi=PREVIEW_DPI).tobytes("png")
            else:
                pix = page.get_pixmap(dpi=SCAN_DPI, colorspace=pymupdf.csGRAY)
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
                img, _angle = deskew(img)
                raw.extend(ocr_page(img, n))
                # The stored page image is the deskewed one, so OCR boxes line up with what the reviewer sees.
                preview = img.resize(
                    (img.width * PREVIEW_DPI // SCAN_DPI, img.height * PREVIEW_DPI // SCAN_DPI)
                )
                images[n] = _png(preview)
        meta: dict[str, Any] = {"digital": digital, "pdf_title": (pdf.metadata or {}).get("title") or None}
        doc = CanonicalDocument(
            id=doc_id,
            job_id=ctx.job_id,
            filename=file.filename,
            mime_type=PDF,
            sha256=file.sha256,
            page_count=pdf.page_count,
            blocks=finalize(raw, doc_id),
        )
        return ParsedDocument(doc, page_images=images, meta=meta)

    def _text_page(self, page: Any, n: int) -> list[RawBlock]:
        w, h = page.rect.width, page.rect.height
        out: list[RawBlock] = []
        table_rects = []
        try:
            tables = page.find_tables().tables
        except Exception:
            tables = []
        for t in tables:
            # Read each cell by clipping the page text to its rectangle: Table.extract() drops spaces in some PDFs.
            cells = [
                [
                    " ".join(page.get_text("text", clip=pymupdf.Rect(c)).split()) if c else ""
                    for c in row.cells
                ]
                for row in t.rows
            ]
            cells = [r for r in cells if any(r)]
            if len(cells) >= 2 and max(len(r) for r in cells) >= 2:
                rect = pymupdf.Rect(t.bbox)
                table_rects.append(rect)
                out.append(
                    RawBlock(
                        BlockType.TABLE,
                        markdown_table(cells),
                        n,
                        (rect.x0 / w, rect.y0 / h, rect.x1 / w, rect.y1 / h),
                        cells=cells,
                    )
                )
        text_blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]
        sizes = [
            s["size"] for b in text_blocks for ln in b["lines"] for s in ln["spans"] if s["text"].strip()
        ]
        body = statistics.median(sizes) if sizes else 10.0
        for b in text_blocks:
            rect = pymupdf.Rect(b["bbox"])
            centre = pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
            if any(tr.contains(centre) for tr in table_rects):
                continue
            lines = ["".join(s["text"] for s in ln["spans"]).strip() for ln in b["lines"]]
            lines = [ln for ln in lines if ln]
            if not lines:
                continue
            size = max((s["size"] for ln in b["lines"] for s in ln["spans"]), default=body)
            text = "\n".join(lines)
            if size >= 1.3 * body and len(text) < 140:
                kind = BlockType.TITLE
            elif sum(1 for ln in lines if _KV.match(ln)) * 2 >= len(lines):
                kind = BlockType.KEY_VALUE
            else:
                kind = BlockType.TEXT
            out.append(RawBlock(kind, text, n, (rect.x0 / w, rect.y0 / h, rect.x1 / w, rect.y1 / h)))
        return out
