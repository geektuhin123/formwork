from __future__ import annotations

from typing import ClassVar

from formwork_core.provider import ModelTier

from formwork_extract.base import LLMExtractor


class VisionExtractor(LLMExtractor):
    """Handwriting, checkboxes, figures and low-confidence OCR regions -> strong model with the cropped region image
    plus the OCR text. Only the regions that need it are sent, never whole pages."""

    name: ClassVar[str] = "vision"
    prefix: ClassVar[str] = "vlm"
    default_tier: ClassVar[ModelTier] = ModelTier.STRONG
    with_images: ClassVar[bool] = True
