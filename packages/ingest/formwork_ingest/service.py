"""ingest_file: any supported file -> CanonicalDocument(s), embedded, with page images and crops stored."""

from __future__ import annotations

import io
import time

from formwork_core.context import JobContext
from formwork_core.errors import FileTooLarge, UnsupportedFile
from formwork_core.logging import get_logger
from formwork_core.models import BlockType
from formwork_core.storage import crop_key, page_key
from formwork_core.telemetry import span
from PIL import Image

from formwork_ingest.base import SUPPORTED, BaseParser, ParsedDocument, StoredFile
from formwork_ingest.parsers import PARSERS, EmailParser

log = get_logger("formwork.ingest")
VISION_TYPES = {BlockType.HANDWRITING, BlockType.CHECKBOX, BlockType.FIGURE}
LOW_OCR = 0.85


def parser_for(mime_type: str, depth: int = 0) -> BaseParser:
    for cls in PARSERS:
        if mime_type in cls.mime_types:
            return EmailParser(depth=depth) if cls is EmailParser else cls()
    raise UnsupportedFile(f"no parser for {mime_type}")


def validate_upload(filename: str, data: bytes, max_bytes: int) -> StoredFile:
    """File-type and size gate used by the API before anything is stored."""
    if not data:
        raise UnsupportedFile("empty file")
    if len(data) > max_bytes:
        raise FileTooLarge(f"file is {len(data)} bytes; the limit is {max_bytes}")
    file = StoredFile(filename=filename[:255] or "upload", data=data)
    if file.mime_type not in SUPPORTED:
        raise UnsupportedFile(f"unsupported file type: {file.mime_type}")
    return file


async def ingest_file(file: StoredFile, ctx: JobContext) -> list[ParsedDocument]:
    """Parse, embed and store artefacts. Returns the document followed by its attachments (depth-first)."""
    started = time.monotonic()
    with span("stage.ingest", tenant_id=ctx.tenant_id, job_id=ctx.job_id, mime_type=file.mime_type):
        parsed = await parser_for(file.mime_type).parse(file, ctx)
        docs = parsed.flatten()
        for p in docs:
            blocks = p.document.blocks
            for i in range(0, len(blocks), 256):  # batched
                batch = blocks[i : i + 256]
                for b, vec in zip(batch, ctx.embedder.embed([b.text[:2000] for b in batch]), strict=True):
                    b.embedding = vec
            for n, png in p.page_images.items():
                await ctx.store.put(page_key(ctx.tenant_id, p.document.id, n), png, "image/png")
            for b in blocks:
                needs_crop = b.type in VISION_TYPES or (
                    b.ocr_confidence is not None and b.ocr_confidence < LOW_OCR
                )
                if needs_crop and b.bbox and b.bbox.page in p.page_images:
                    img = Image.open(io.BytesIO(p.page_images[b.bbox.page]))
                    pad = 0.01
                    crop = img.crop(
                        (
                            int(max(0, b.bbox.x0 - pad) * img.width),
                            int(max(0, b.bbox.y0 - pad) * img.height),
                            int(min(1, b.bbox.x1 + pad) * img.width),
                            int(min(1, b.bbox.y1 + pad) * img.height),
                        )
                    )
                    buf = io.BytesIO()
                    crop.save(buf, format="PNG")
                    await ctx.store.put(
                        crop_key(ctx.tenant_id, p.document.id, b.id), buf.getvalue(), "image/png"
                    )
    log.info(
        "ingested",
        filename=file.filename,
        documents=len(docs),
        blocks=sum(len(p.document.blocks) for p in docs),
        ms=int((time.monotonic() - started) * 1000),
    )
    return docs
