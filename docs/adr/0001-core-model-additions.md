# ADR 0001: Optional additions to the core models

Status: accepted (Phase 0)

The six contract models follow docs/lld.md. These additions are optional fields with defaults, so the LLD shape still validates.

- `Extraction.id`, `alternatives`, `reason`, `signals`. The storage schema already has `alternatives`; the review UI needs an id; `reason` explains a failed validation; `signals` keeps the confidence inputs so merge and validate can re-blend the score.
- `Alternative` model: a losing value with its own evidence. An alternative without evidence is rejected, same as an Extraction.
- `OutputFormat`, `ExtractionStatus` enums (named but not listed in the LLD).
- `Extraction.record_key` is a readable slug of the normalised entity-key values (`inv-2026-0042`), falling back to a hash above 96 characters. The LLD says "hash"; the lineage example in the same document shows a readable key, and reviewers need to recognise records.
- `Evidence` and `Block` enforce "bbox or locator"; `Evidence.quote` must be non-empty.
- A required field with no value is not an `Extraction` (it would have no evidence). It is a row in `review_gaps` and shows in the review queue; a reviewer-entered value becomes an Extraction whose evidence is the reviewer's attestation (`locator="manual:<reviewer>"`).
