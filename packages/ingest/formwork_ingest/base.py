"""Ingest contracts: StoredFile in, ParsedDocument out. Nothing downstream of ingest ever reads a raw file."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, ClassVar, Protocol

from formwork_core.context import JobContext
from formwork_core.errors import FileTooLarge, UnsupportedFile
from formwork_core.ids import stable_id
from formwork_core.models import BBox, Block, BlockType, CanonicalDocument

PARSER_VERSION = "2026.11.1"
MAX_PAGES = 300
MAX_SHEET_CELLS = 3_000_000
MAX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024
MAX_IMAGE_PIXELS = 60_000_000
MAX_ATTACHMENT_DEPTH = 3
MAX_ATTACHMENTS = 20

PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
CSV = "text/csv"
EML = "message/rfc822"
IMAGE_TYPES = {"image/png", "image/jpeg", "image/tiff", "image/webp", "image/bmp"}
SUPPORTED = {PDF, XLSX, DOCX, CSV, EML, *IMAGE_TYPES}

_EXT = {
    ".pdf": PDF,
    ".xlsx": XLSX,
    ".xlsm": XLSX,
    ".docx": DOCX,
    ".csv": CSV,
    ".tsv": CSV,
    ".eml": EML,
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


@dataclass
class StoredFile:
    filename: str
    data: bytes
    mime_type: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        if not self.sha256:
            self.sha256 = hashlib.sha256(self.data).hexdigest()
        if not self.mime_type:
            self.mime_type = detect_mime(self.filename, self.data)


@dataclass
class ParsedDocument:
    document: CanonicalDocument
    parent_document_id: str | None = None
    page_images: dict[int, bytes] = field(default_factory=dict)  # 1-based page -> PNG
    meta: dict[str, Any] = field(default_factory=dict)
    children: list[ParsedDocument] = field(default_factory=list)

    def flatten(self) -> list[ParsedDocument]:
        out = [self]
        for c in self.children:
            out.extend(c.flatten())
        return out


class Parser(Protocol):
    mime_types: ClassVar[set[str]]

    async def parse(self, file: StoredFile, ctx: JobContext) -> ParsedDocument: ...


class BaseParser:
    """Parsers are CPU-bound; `parse` runs the synchronous implementation in a worker thread."""

    mime_types: ClassVar[set[str]] = set()

    async def parse(self, file: StoredFile, ctx: JobContext) -> ParsedDocument:
        return await asyncio.to_thread(self.parse_sync, file, ctx)

    def parse_sync(self, file: StoredFile, ctx: JobContext) -> ParsedDocument:
        raise NotImplementedError


def detect_mime(filename: str, data: bytes) -> str:
    """Trust content over the claimed name: magic bytes first, extension only to split zip/text families."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if head[:2] == b"BM" and ext == ".bmp":
        return "image/bmp"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                names = set(z.namelist())
                if sum(i.file_size for i in z.infolist()) > MAX_UNCOMPRESSED_BYTES:
                    raise FileTooLarge("archive expands beyond the allowed size")
        except zipfile.BadZipFile:
            raise UnsupportedFile("corrupt Office file") from None
        if "xl/workbook.xml" in names:
            return XLSX
        if "word/document.xml" in names:
            return DOCX
        raise UnsupportedFile("unsupported zip-based file")
    if b"\x00" in data[:4096]:
        raise UnsupportedFile(f"unsupported binary file: {filename}")
    if ext == ".eml" or (ext not in (".csv", ".tsv") and _looks_like_email(data)):
        return EML
    if ext in (".csv", ".tsv", ".txt") or mimetypes.guess_type(filename)[0] == CSV:
        return CSV
    raise UnsupportedFile(f"unsupported file type: {filename}")


def _looks_like_email(data: bytes) -> bool:
    head = data[:2048].lower()
    return sum(
        h in head for h in (b"\nfrom:", b"\nto:", b"\nsubject:", b"mime-version:", b"\ndate:")
    ) >= 3 or head.startswith(b"from:")


def document_id(ctx: JobContext, sha256: str, parent: str | None = None) -> str:
    return stable_id("doc", ctx.tenant_id, sha256, parent or "", PARSER_VERSION)


@dataclass
class RawBlock:
    """Parser output before ids and reading order are assigned. Coordinates are normalised 0..1."""

    type: BlockType
    text: str
    page: int | None = None
    box: tuple[float, float, float, float] | None = None
    locator: str | None = None
    cells: list[list[str]] | None = None
    ocr_confidence: float | None = None


def _esc(s: object) -> str:
    return str(s).replace("|", "/").replace("\n", " ").strip()


def markdown_table(cells: list[list[str]], limit: int | None = None) -> str:
    if not cells:
        return ""
    width = max(len(r) for r in cells)
    rows = [[_esc(c) for c in r] + [""] * (width - len(r)) for r in cells]
    shown = rows if limit is None else rows[: limit + 1]
    out = ["| " + " | ".join(shown[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in shown[1:]]
    if limit is not None and len(rows) > limit + 1:
        out.append(f"(+{len(rows) - limit - 1} more rows)")
    return "\n".join(out)


def finalize(raw: list[RawBlock], doc_id: str) -> list[Block]:
    """Assign reading order and stable ids. Blocks with no text are dropped."""
    from formwork_ingest.order import reading_order

    ordered = reading_order([r for r in raw if r.text.strip()])
    blocks = []
    for i, r in enumerate(ordered):
        bbox = None
        if r.box is not None and r.page is not None:
            x0, y0, x1, y1 = (min(1.0, max(0.0, v)) for v in r.box)
            bbox = BBox(page=r.page, x0=round(x0, 4), y0=round(y0, 4), x1=round(x1, 4), y1=round(y1, 4))
        blocks.append(
            Block(
                id=stable_id("blk", doc_id, i),
                document_id=doc_id,
                type=r.type,
                text=r.text.strip(),
                cells=r.cells,
                bbox=bbox,
                locator=r.locator,
                reading_order=i,
                ocr_confidence=None if r.ocr_confidence is None else round(r.ocr_confidence, 3),
            )
        )
    return blocks
