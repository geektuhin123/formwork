"""Block embeddings behind an interface.

Anthropic has no embeddings endpoint, so the MVP default is a deterministic feature-hashing embedder
(word unigrams + character trigrams, signed, L2-normalised, 1024-d). It needs no network, is stable across
runs, and is good enough as the dense half of hybrid retrieval. Swap in a hosted model via `Embedder`.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

EMBEDDING_DIM = 1024
_TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class HashingEmbedder:
    dim = EMBEDDING_DIM

    def _features(self, text: str) -> list[str]:
        toks = tokenize(text)
        feats = list(toks)
        for t in toks:
            padded = f"^{t}$"
            feats.extend(padded[i : i + 3] for i in range(len(padded) - 2))
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat in self._features(text):
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
                out[row, h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
            norm = float(np.linalg.norm(out[row]))
            if norm > 0:
                out[row] /= norm
        return [[round(float(x), 5) for x in r] for r in out]
