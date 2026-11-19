"""Prompt-injection defence for document text.

Document text is untrusted. Three layers, none of which relies on the model behaving:
1. `mask` removes instruction-like sentences before the text reaches a prompt.
2. `overlaps` lets the extractor reject any value whose evidence quote sits on a flagged span.
3. Downstream, the value-in-quote check and schema rules catch what a novel phrasing gets past 1 and 2.
"""

from __future__ import annotations

import re

MASK = "[instruction-like text removed]"

_PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|your|the)\b[^.\n]{0,30}\b(instruction|prompt|rule|direction|guideline)s?\b",
    r"^\s*(system|assistant|developer)\s*(prompt|message)?\s*:",
    r"\byou (are|must) now\b",
    r"\bnew instructions?\b\s*:",
    r"\b(do not|don't|never) (extract|report|return|mention)\b",
    r"\b(set|change|update|make|report|return)\b[^.\n]{0,40}\bto\b[^.\n]{0,40}\b(instruction|assistant|model|ai|llm|extractor|system)\b",
    r"\b(ai|llm|language model|assistant|extractor)\b[^.\n]{0,60}\b(must|should|shall|please)\b[^.\n]{0,40}\b(set|change|return|report|output|use)\b",
]
_RE = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.I | re.M)


def find_injections(text: str) -> list[tuple[int, int]]:
    """Spans (start, end) of whole sentences/lines that contain instruction-like text."""
    spans: list[tuple[int, int]] = []
    for m in _RE.finditer(text):
        start = max(text.rfind("\n", 0, m.start()), text.rfind(". ", 0, m.start()) + 1) + 1
        ends = [e for e in (text.find("\n", m.end()), text.find(". ", m.end())) if e != -1]
        end = min(ends) + 1 if ends else len(text)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((max(0, start), end))
    return spans


def mask(text: str) -> tuple[str, list[tuple[int, int]]]:
    spans = find_injections(text)
    if not spans:
        return text, spans
    out, pos = [], 0
    for s, e in spans:
        out.append(text[pos:s])
        out.append(MASK)
        pos = e
    out.append(text[pos:])
    return "".join(out), spans


def overlaps(quote: str, text: str, spans: list[tuple[int, int]]) -> bool:
    """True when the quote lies (even partly) on a flagged span of the original text."""
    if not spans or not quote.strip():
        return False
    q = quote.strip()
    idx = text.find(q)
    if idx == -1:
        low = text.casefold()
        idx = low.find(q.casefold())
    if idx == -1:
        return any(
            q.casefold() in text[s:e].casefold() or text[s:e].strip().casefold() in q.casefold()
            for s, e in spans
        )
    return any(idx < e and s < idx + len(q) for s, e in spans)
