"""Cross-source merge."""

from formwork_merge.keys import PROVISIONAL_PREFIX, UNKEYED_PREFIX, resolve_keys
from formwork_merge.merge import MergePolicy, MergeResult, SourceInfo, match_keys, merge, resolve_field

__all__ = [
    "PROVISIONAL_PREFIX",
    "UNKEYED_PREFIX",
    "MergePolicy",
    "MergeResult",
    "SourceInfo",
    "match_keys",
    "merge",
    "resolve_field",
    "resolve_keys",
]
