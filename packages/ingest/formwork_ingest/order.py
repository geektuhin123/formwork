"""Reading order: top-to-bottom, column-aware.

Per page, recursively: (1) if a vertical gutter splits the region, read left then right; (2) otherwise full-width
"spanner" blocks (titles, tables) cut the region into horizontal bands, each read recursively; (3) otherwise sort
by (y, x). Trying the gutter before horizontal cuts keeps two-column text correct even when paragraph gaps line up
across the columns. Blocks without coordinates (sheets, email, docx) keep parser order.
"""

from __future__ import annotations

from typing import Protocol


class _Boxed(Protocol):
    page: int | None
    box: tuple[float, float, float, float] | None


MIN_GUTTER = 0.025


def _gutter(items: list[_Boxed]) -> float | None:
    spans = sorted((b.box[0], b.box[2]) for b in items if b.box)
    best_gap, cut, reach = 0.0, None, spans[0][1]
    for x0, x1 in spans[1:]:
        if x0 - reach > best_gap:
            best_gap, cut = x0 - reach, (x0 + reach) / 2
        reach = max(reach, x1)
    return cut if best_gap >= MIN_GUTTER else None


def _side_by_side(left: list[_Boxed], right: list[_Boxed]) -> bool:
    """Columns must overlap vertically; a footer bottom-left and totals mid-right are not two columns."""
    l0, l1 = min(b.box[1] for b in left if b.box), max(b.box[3] for b in left if b.box)
    r0, r1 = min(b.box[1] for b in right if b.box), max(b.box[3] for b in right if b.box)
    overlap = min(l1, r1) - max(l0, r0)
    return overlap >= 0.3 * max(min(l1 - l0, r1 - r0), 1e-6)


def _order(items: list[_Boxed]) -> list[_Boxed]:
    if len(items) <= 1:
        return items
    cut = _gutter(items)
    if cut is not None:
        left = [b for b in items if b.box and (b.box[0] + b.box[2]) / 2 < cut]
        right = [b for b in items if b not in left]
        if left and right and _side_by_side(left, right):
            return _order(left) + _order(right)
    x_min = min(b.box[0] for b in items if b.box)
    x_max = max(b.box[2] for b in items if b.box)
    width = max(x_max - x_min, 1e-6)
    spanners = sorted(
        (b for b in items if b.box and (b.box[2] - b.box[0]) > 0.55 * width),
        key=lambda b: b.box[1] if b.box else 0,
    )
    rest = [b for b in items if b not in spanners]
    if spanners and rest and len(spanners) < len(items):
        out: list[_Boxed] = []
        for sp in spanners:
            assert sp.box
            band = [b for b in rest if b.box and (b.box[1] + b.box[3]) / 2 < sp.box[1]]
            rest = [b for b in rest if b not in band]
            out += _order(band) if len(band) < len(items) else sorted(band, key=_yx)
            out.append(sp)
        out += _order(rest) if len(rest) < len(items) else sorted(rest, key=_yx)
        return out
    return sorted(items, key=_yx)


def _yx(b: _Boxed) -> tuple[float, float]:
    assert b.box
    return (round(b.box[1], 3), b.box[0])


def reading_order[T: _Boxed](blocks: list[T]) -> list[T]:
    out: list[T] = []
    pages: dict[int, list[T]] = {}
    for b in blocks:
        if b.box is None or b.page is None:
            continue
        pages.setdefault(b.page, []).append(b)
    placed = False
    for b in blocks:  # keep non-positional blocks in parser order, positional ones page by page
        if b.box is None or b.page is None:
            out.append(b)
        elif not placed:
            placed = True
            for page in sorted(pages):
                out.extend(_order(list(pages[page])))  # type: ignore[arg-type]
    return out
