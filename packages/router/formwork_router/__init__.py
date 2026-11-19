"""Router: the cheapest extractor that can handle a block (docs/lld.md, "4. Router")."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from formwork_core.models import Block, BlockType
from formwork_core.provider import ModelTier

TABLE_MIN_OCR = 0.9
TEXT_MIN_OCR = 0.85
VISION_TYPES = {BlockType.HANDWRITING, BlockType.CHECKBOX, BlockType.FIGURE}


class ExtractorKind(StrEnum):
    TABULAR = "tabular"
    TABLE = "table"
    TEXT = "text"
    VISION = "vision"


@dataclass(frozen=True)
class Route:
    extractor: ExtractorKind
    tier: ModelTier | None  # None: no model call per block (tabular plan execution)

    def escalated(self) -> Route:
        """Same extractor, next model up (used after a failed validation)."""
        return self if self.tier is None else Route(self.extractor, self.tier.next_up())


def route_block(block: Block) -> Route:
    conf = block.ocr_confidence
    if block.type is BlockType.SHEET_RANGE:
        return Route(ExtractorKind.TABULAR, None)
    if block.type in VISION_TYPES:
        return Route(ExtractorKind.VISION, ModelTier.STRONG)
    if block.type is BlockType.TABLE:
        if block.cells and (conf is None or conf >= TABLE_MIN_OCR):
            return Route(ExtractorKind.TABLE, ModelTier.CHEAP)
        return Route(ExtractorKind.VISION, ModelTier.STRONG)
    if conf is None or conf >= TEXT_MIN_OCR:
        return Route(ExtractorKind.TEXT, ModelTier.CHEAP)
    return Route(ExtractorKind.VISION, ModelTier.STRONG)


def partition(blocks: list[Block]) -> dict[ExtractorKind, list[Block]]:
    out: dict[ExtractorKind, list[Block]] = {k: [] for k in ExtractorKind}
    for b in blocks:
        out[route_block(b).extractor].append(b)
    return out


__all__ = ["ExtractorKind", "Route", "partition", "route_block"]
