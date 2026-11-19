from __future__ import annotations

import io
from typing import ClassVar

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from formwork_core.context import JobContext
from formwork_core.errors import UnsupportedFile
from formwork_core.models import BlockType, CanonicalDocument

from formwork_ingest.base import (
    DOCX,
    BaseParser,
    ParsedDocument,
    RawBlock,
    StoredFile,
    document_id,
    finalize,
    markdown_table,
)


class DocxParser(BaseParser):
    mime_types: ClassVar[set[str]] = {DOCX}

    def parse_sync(self, file: StoredFile, ctx: JobContext) -> ParsedDocument:
        try:
            d = docx.Document(io.BytesIO(file.data))
        except Exception as exc:
            raise UnsupportedFile(f"cannot open DOCX: {exc}") from exc
        raw: list[RawBlock] = []
        p_i = t_i = 0
        for item in d.iter_inner_content():
            if isinstance(item, Paragraph):
                text = item.text.strip()
                if text:
                    style = (item.style.name or "").lower() if item.style is not None else ""
                    kind = BlockType.TITLE if style.startswith(("heading", "title")) else BlockType.TEXT
                    if kind is BlockType.TEXT and ":" in text[:45] and len(text) < 160:
                        kind = BlockType.KEY_VALUE
                    raw.append(RawBlock(kind, text, locator=f"docx.p[{p_i}]"))
                p_i += 1
            elif isinstance(item, Table):
                cells = [[c.text.strip() for c in row.cells] for row in item.rows]
                if cells:
                    raw.append(
                        RawBlock(
                            BlockType.TABLE, markdown_table(cells), locator=f"docx.table[{t_i}]", cells=cells
                        )
                    )
                t_i += 1
        doc_id = document_id(ctx, file.sha256)
        doc = CanonicalDocument(
            id=doc_id,
            job_id=ctx.job_id,
            filename=file.filename,
            mime_type=DOCX,
            sha256=file.sha256,
            page_count=1,
            blocks=finalize(raw, doc_id),
        )
        created = d.core_properties.created
        return ParsedDocument(doc, meta={"document_date": created.date().isoformat() if created else None})
