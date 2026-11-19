"""Classify documents and pick the blocks each field group needs, so prompts stay small."""

from formwork_retrieval.retrieval import BM25, classify_document, group_query, select_blocks

__all__ = ["BM25", "classify_document", "group_query", "select_blocks"]
