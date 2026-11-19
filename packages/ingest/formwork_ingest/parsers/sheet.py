from __future__ import annotations

import io
from datetime import date, datetime, time
from typing import Any, ClassVar

import numpy as np
import openpyxl
from formwork_core.context import JobContext
from formwork_core.errors import UnsupportedFile
from formwork_core.models import BlockType, CanonicalDocument
from openpyxl.utils import get_column_letter

from formwork_ingest.base import (
    MAX_SHEET_CELLS,
    XLSX,
    BaseParser,
    ParsedDocument,
    RawBlock,
    StoredFile,
    document_id,
    finalize,
    markdown_table,
)

PREVIEW_ROWS = 30


def cell_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0, 0) else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    return str(v).strip()


def _cuts(occupied: np.ndarray) -> list[tuple[int, int]]:
    """Runs of True in a 1-d mask -> [(start, end_exclusive)]."""
    padded = np.concatenate([[False], occupied, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]


def find_islands(mask: np.ndarray, depth: int = 0) -> list[tuple[int, int, int, int]]:
    """Table islands by recursive XY-cut on the non-empty mask -> [(r0, c0, r1, c1)], end exclusive."""
    if not mask.any():
        return []
    rows = _cuts(mask.any(axis=1))
    out: list[tuple[int, int, int, int]] = []
    for r0, r1 in rows:
        cols = _cuts(mask[r0:r1].any(axis=0))
        for c0, c1 in cols:
            sub = mask[r0:r1, c0:c1]
            inner_rows = _cuts(sub.any(axis=1))
            if depth < 6 and len(inner_rows) > 1:
                out += [(r0 + a, c0 + b, r0 + c, c0 + d) for a, b, c, d in find_islands(sub, depth + 1)]
            else:  # trim to the rows this column band actually occupies
                out.append((r0 + inner_rows[0][0], c0, r0 + inner_rows[-1][1], c1))
    return out


def looks_like_header(row: list[str]) -> bool:
    filled = [c for c in row if c]
    if not filled:
        return False
    texty = sum(1 for c in filled if not c.replace(",", "").replace(".", "").replace("-", "").isdigit())
    return texty / len(filled) >= 0.6


def island_block(
    sheet: str, grid: list[list[str]], r0: int, c0: int, r1: int, c1: int, row_offset: int = 0
) -> RawBlock:
    cells = [row[c0:c1] for row in grid[r0:r1]]
    locator = (
        f"{sheet}!{get_column_letter(c0 + 1)}{r0 + 1 + row_offset}:{get_column_letter(c1)}{r1 + row_offset}"
    )
    if len(cells) >= 2 and (c1 - c0) >= 2 and looks_like_header(cells[0]):
        return RawBlock(
            BlockType.SHEET_RANGE, markdown_table(cells, limit=PREVIEW_ROWS), locator=locator, cells=cells
        )
    text = "\n".join(" ".join(c for c in row if c) for row in cells)
    return RawBlock(BlockType.TEXT, text, locator=locator)


class SpreadsheetParser(BaseParser):
    mime_types: ClassVar[set[str]] = {XLSX}

    def parse_sync(self, file: StoredFile, ctx: JobContext) -> ParsedDocument:
        try:
            wb = openpyxl.load_workbook(io.BytesIO(file.data), data_only=True)
        except Exception as exc:
            raise UnsupportedFile(f"cannot open workbook: {exc}") from exc
        doc_id = document_id(ctx, file.sha256)
        raw: list[RawBlock] = []
        for ws in wb.worksheets:
            if ws.sheet_state != "visible" or ws.max_row * ws.max_column == 0:
                continue
            if ws.max_row * ws.max_column > MAX_SHEET_CELLS:
                raise UnsupportedFile(f"sheet {ws.title!r} is too large")
            grid = [[cell_str(v) for v in row] for row in ws.iter_rows(values_only=True)]
            width = max((len(r) for r in grid), default=0)
            grid = [r + [""] * (width - len(r)) for r in grid]
            for rng in ws.merged_cells.ranges:  # unmerge: repeat the top-left value across the range
                value = grid[rng.min_row - 1][rng.min_col - 1]
                for r in range(rng.min_row - 1, rng.max_row):
                    for c in range(rng.min_col - 1, rng.max_col):
                        grid[r][c] = value
            mask = (
                np.array([[bool(c) for c in row] for row in grid], dtype=bool)
                if grid
                else np.zeros((0, 0), dtype=bool)
            )
            for r0, c0, r1, c1 in find_islands(mask):
                raw.append(island_block(ws.title, grid, r0, c0, r1, c1))
        doc = CanonicalDocument(
            id=doc_id,
            job_id=ctx.job_id,
            filename=file.filename,
            mime_type=XLSX,
            sha256=file.sha256,
            page_count=len(wb.worksheets),
            blocks=finalize(raw, doc_id),
        )
        return ParsedDocument(doc, meta={"sheets": [ws.title for ws in wb.worksheets]})
