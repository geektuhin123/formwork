from __future__ import annotations

from typing import ClassVar

from formwork_core.provider import ModelTier

from formwork_extract.base import LLMExtractor


class TableExtractor(LLMExtractor):
    """TABLE blocks with clean cells -> cheap model, cells as markdown, 40 rows per call with the header repeated."""

    name: ClassVar[str] = "table"
    prefix: ClassVar[str] = "llm"
    default_tier: ClassVar[ModelTier] = ModelTier.CHEAP
