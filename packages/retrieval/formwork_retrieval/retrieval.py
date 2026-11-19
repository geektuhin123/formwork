from __future__ import annotations

import math
from collections import Counter

import numpy as np
from formwork_core.context import JobContext
from formwork_core.embedding import Embedder, tokenize
from formwork_core.groups import FieldGroup
from formwork_core.models import Block, CanonicalDocument
from formwork_core.prompting import DOC_CLASSES, UNTRUSTED_NOTICE, render_blocks
from formwork_core.provider import LLMRequest, ModelTier, ToolSpec
from formwork_core.telemetry import span

SKIP_RETRIEVAL_PAGES = 6
SKIP_RETRIEVAL_BLOCKS = 80
CLASSIFY_CHARS = 4000

_CLASSIFY_TOOL = ToolSpec(
    name="classify_document",
    description="Classify a business document from its first two pages.",
    input_schema={
        "type": "object",
        "required": ["doc_class", "confidence"],
        "properties": {
            "doc_class": {"type": "string", "enum": DOC_CLASSES},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
)


async def classify_document(doc: CanonicalDocument, ctx: JobContext) -> str:
    """One cheap call per document on its first two pages. The class selects few-shot examples and routing hints."""
    head: list[Block] = []
    used = 0
    for b in doc.blocks:
        if b.bbox is not None and b.bbox.page > 2:
            continue
        if used + len(b.text) > CLASSIFY_CHARS and head:
            break
        head.append(b.model_copy(update={"text": b.text[:1500]}))
        used += len(b.text)
    if not head:
        return "other"
    with span("stage.classify", tenant_id=ctx.tenant_id, job_id=ctx.job_id, document_id=doc.id):
        resp = await ctx.provider.complete(
            LLMRequest(
                tier=ModelTier.CHEAP,
                purpose="classify",
                tool=_CLASSIFY_TOOL,
                max_tokens=256,
                system="You classify business documents. " + UNTRUSTED_NOTICE,
                prompt=f"File name: {doc.filename}\nClassify this document.\n{render_blocks(head)}",
            )
        )
    cls = str(resp.output.get("doc_class", "other"))
    return cls if cls in DOC_CLASSES else "other"


class BM25:
    """Okapi BM25 over a small in-memory corpus (one document's blocks)."""

    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = [tokenize(t) for t in texts]
        self.k1, self.b = k1, b
        self.avg = sum(len(d) for d in self.docs) / max(1, len(self.docs))
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query: str) -> list[float]:
        q = set(tokenize(query))
        out = []
        for d, tf in zip(self.docs, self.tf, strict=True):
            norm = self.k1 * (1 - self.b + self.b * len(d) / max(self.avg, 1e-9))
            out.append(
                sum(self.idf.get(t, 0.0) * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in q if t in tf)
            )
        return out


def group_query(group: FieldGroup) -> str:
    return " ".join(
        f"{f.name.replace('_', ' ')} {f.description} {' '.join(f.examples[:2])}" for f in group.fields
    )


def select_blocks(
    doc: CanonicalDocument,
    group: FieldGroup,
    embedder: Embedder,
    top_k: int = 12,
    neighbours: int = 1,
    force_all: bool = False,
) -> list[Block]:
    """Hybrid BM25 + cosine top-k plus reading-order neighbours. Small documents skip retrieval entirely."""
    blocks = doc.blocks
    if (
        force_all
        or (doc.page_count < SKIP_RETRIEVAL_PAGES and len(blocks) <= SKIP_RETRIEVAL_BLOCKS)
        or len(blocks) <= top_k
    ):
        return list(blocks)
    query = group_query(group)
    bm = np.array(BM25([b.text for b in blocks]).scores(query), dtype=np.float32)
    if bm.max() > 0:
        bm = bm / bm.max()
    missing = [i for i, b in enumerate(blocks) if b.embedding is None]
    vectors = (
        {i: v for i, v in zip(missing, embedder.embed([blocks[i].text[:2000] for i in missing]), strict=True)}
        if missing
        else {}
    )
    mat = np.array(
        [b.embedding if b.embedding is not None else vectors[i] for i, b in enumerate(blocks)],
        dtype=np.float32,
    )
    q = np.array(embedder.embed([query])[0], dtype=np.float32)
    cos = np.clip(mat @ q, 0.0, 1.0)
    hybrid = 0.5 * bm + 0.5 * cos
    keep: set[int] = set()
    for i in np.argsort(-hybrid)[:top_k]:
        keep.update(range(max(0, int(i) - neighbours), min(len(blocks), int(i) + neighbours + 1)))
    return [blocks[i] for i in sorted(keep)]
