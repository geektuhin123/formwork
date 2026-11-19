from __future__ import annotations

import csv
import io
from typing import ClassVar

from formwork_core.context import JobContext
from formwork_core.errors import UnsupportedFile
from formwork_core.models import CanonicalDocument

from formwork_ingest.base import (
    CSV,
    MAX_SHEET_CELLS,
    BaseParser,
    ParsedDocument,
    StoredFile,
    document_id,
    finalize,
)
from formwork_ingest.parsers.sheet import island_block


class CsvParser(BaseParser):
    mime_types: ClassVar[set[str]] = {CSV}

    def parse_sync(self, file: StoredFile, ctx: JobContext) -> ParsedDocument:
        try:
            text = file.data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = file.data.decode("latin-1")
        try:
            dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]
        rows = [r for r in rows if any(r)]
        if not rows:
            raise UnsupportedFile("CSV file is empty")
        width = max(len(r) for r in rows)
        if width * len(rows) > MAX_SHEET_CELLS:
            raise UnsupportedFile("CSV file is too large")
        grid = [r + [""] * (width - len(r)) for r in rows]
        doc_id = document_id(ctx, file.sha256)
        raw = [island_block("csv", grid, 0, 0, len(grid), width)]
        doc = CanonicalDocument(
            id=doc_id,
            job_id=ctx.job_id,
            filename=file.filename,
            mime_type=CSV,
            sha256=file.sha256,
            page_count=1,
            blocks=finalize(raw, doc_id),
        )
        return ParsedDocument(doc)
