"""The repair pass: failing or weak fields go back for re-extraction with the error message, the whole document as
the window, and the next model up. Attempt 2 also attaches the page image so the model can re-read the pixels."""

from __future__ import annotations

from formwork_core.context import JobContext
from formwork_core.errors import NotFound
from formwork_core.groups import FieldGroup, field_groups
from formwork_core.models import CanonicalDocument, Extraction, TargetSchema
from formwork_core.paths import spec_path
from formwork_core.provider import ModelTier
from formwork_core.storage import page_key
from formwork_extract import PlanCache, VisionExtractor
from formwork_validate import RepairTarget

from formwork_pipeline.extract import extract_group

MAX_DOCS_PER_TARGET = 3
MAX_PAGE_IMAGES = 2


class PipelineRepairer:
    def __init__(
        self,
        schema: TargetSchema,
        docs: dict[str, CanonicalDocument],
        record_sources: dict[str, list[str]],
        ctx: JobContext,
        plan_cache: PlanCache | None = None,
    ) -> None:
        self.schema, self.docs, self.record_sources, self.ctx, self.plan_cache = (
            schema,
            docs,
            record_sources,
            ctx,
            plan_cache,
        )

    def _docs_for(self, t: RepairTarget) -> list[str]:
        if t.current is not None:
            ids = list(dict.fromkeys(ev.document_id for ev in t.current.evidence))
        else:
            ids = list(self.record_sources.get(t.record_key, []))
        return [d for d in ids if d in self.docs][:MAX_DOCS_PER_TARGET]

    async def repair(self, targets: list[RepairTarget], attempt: int) -> list[Extraction]:
        groups = field_groups(self.schema)
        batches: dict[tuple[str, str], list[RepairTarget]] = {}
        for t in targets:
            want = spec_path(t.field_path)
            group = next(
                (g for g in groups if any(f.path == want for f in g.fields) or g.array_path == want), None
            )
            if group is None:
                continue
            for doc_id in self._docs_for(t):
                batches.setdefault((doc_id, group.name), []).append(t)
        best: dict[tuple[str, str], Extraction] = {}
        for (doc_id, group_name), ts in batches.items():
            doc = self.docs[doc_id]
            group = next(g for g in groups if g.name == group_name)
            if not group.is_array:  # re-ask only for the failing fields
                wanted = {spec_path(t.field_path) for t in ts}
                group = FieldGroup(name=group.name, fields=tuple(f for f in group.fields if f.path in wanted))
            feedback = "\n".join(
                f"- {t.field_path}: {t.error}"
                + (f" (previous value: {t.current.value!r})" if t.current else "")
                for t in ts
            )
            if attempt >= 2 and any(b.bbox is not None for b in doc.blocks):
                images = []
                for n in sorted({b.bbox.page for b in doc.blocks if b.bbox})[:MAX_PAGE_IMAGES]:
                    try:
                        images.append(await self.ctx.store.get(page_key(self.ctx.tenant_id, doc.id, n)))
                    except NotFound:
                        continue
                report = await VisionExtractor().run(
                    group,
                    doc.blocks,
                    self.ctx,
                    feedback=feedback,
                    doc_class=doc.doc_class,
                    attempt=attempt + 1,
                    extra_images=images or None,
                )
                found = report.extractions
            else:
                found = (
                    await extract_group(
                        self.schema,
                        doc,
                        group,
                        self.ctx,
                        self.plan_cache,
                        tier=ModelTier.STRONG,
                        feedback=feedback,
                        widen=True,
                        attempt=attempt + 1,
                    )
                ).extractions
            by_path = {e.field_path: e for e in found}
            for t in ts:
                fix = by_path.get(t.field_path)
                if fix is None or fix.normalized_value is None:
                    continue
                k = (t.record_key, t.field_path)
                if k not in best or fix.confidence > best[k].confidence:
                    best[k] = fix.model_copy(update={"record_key": t.record_key})
        return list(best.values())
