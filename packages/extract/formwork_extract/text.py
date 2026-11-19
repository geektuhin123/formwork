from __future__ import annotations

from typing import ClassVar

from formwork_core.provider import ModelTier

from formwork_extract.base import LLMExtractor


class TextExtractor(LLMExtractor):
    """TEXT / KEY_VALUE blocks with good OCR confidence -> cheap model."""

    name: ClassVar[str] = "text"
    prefix: ClassVar[str] = "llm"
    default_tier: ClassVar[ModelTier] = ModelTier.CHEAP
