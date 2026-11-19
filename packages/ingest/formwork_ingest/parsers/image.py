from __future__ import annotations

import io
from typing import ClassVar

from formwork_core.context import JobContext
from formwork_core.errors import UnsupportedFile
from formwork_core.models import CanonicalDocument
from PIL import Image

from formwork_ingest.base import (
    IMAGE_TYPES,
    MAX_IMAGE_PIXELS,
    BaseParser,
    ParsedDocument,
    StoredFile,
    document_id,
    finalize,
)
from formwork_ingest.ocr import deskew, fix_rotation, ocr_page

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ImageParser(BaseParser):
    """An image is one scanned page: rotation correction, deskew, then the same OCR path as scanned PDFs."""

    mime_types: ClassVar[set[str]] = set(IMAGE_TYPES)

    def parse_sync(self, file: StoredFile, ctx: JobContext) -> ParsedDocument:
        try:
            opened = Image.open(io.BytesIO(file.data))
            opened.load()
        except Exception as exc:
            raise UnsupportedFile(f"cannot read image: {exc}") from exc
        img: Image.Image = opened.convert("L")
        longest = max(img.size)
        if longest < 1400:  # small photos OCR badly; upsample towards ~200 DPI for an A4 page
            f = min(3.0, 1700 / longest)
            img = img.resize((int(img.width * f), int(img.height * f)), Image.Resampling.LANCZOS)
        img, rotated = fix_rotation(img)
        img, angle = deskew(img)
        doc_id = document_id(ctx, file.sha256)
        blocks = finalize(ocr_page(img, 1), doc_id)
        buf = io.BytesIO()
        preview = (
            img
            if max(img.size) <= 1400
            else img.resize((img.width * 1400 // max(img.size), img.height * 1400 // max(img.size)))
        )
        preview.save(buf, format="PNG")
        doc = CanonicalDocument(
            id=doc_id,
            job_id=ctx.job_id,
            filename=file.filename,
            mime_type=file.mime_type,
            sha256=file.sha256,
            page_count=1,
            blocks=blocks,
        )
        return ParsedDocument(
            doc,
            page_images={1: buf.getvalue()},
            meta={"digital": False, "rotation": rotated, "skew": round(angle, 2)},
        )
