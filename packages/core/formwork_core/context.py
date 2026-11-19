from __future__ import annotations

from dataclasses import dataclass, field

from formwork_core.config import Settings, get_settings
from formwork_core.embedding import Embedder, HashingEmbedder
from formwork_core.provider import Budget, FakeProvider, ModelProvider
from formwork_core.storage import MemoryObjectStore, ObjectStore


@dataclass
class JobContext:
    """Everything a pipeline stage may touch besides its inputs."""

    tenant_id: str
    job_id: str
    provider: ModelProvider
    store: ObjectStore
    settings: Settings = field(default_factory=get_settings)
    embedder: Embedder = field(default_factory=HashingEmbedder)
    budget: Budget = field(default_factory=lambda: Budget(None))

    @classmethod
    def for_tests(cls, provider: ModelProvider | None = None, **kw: object) -> JobContext:
        return cls(
            tenant_id=str(kw.get("tenant_id", "ten_test")),
            job_id=str(kw.get("job_id", "job_test")),
            provider=provider or FakeProvider(),
            store=MemoryObjectStore(),
            settings=Settings(env="test"),
        )
