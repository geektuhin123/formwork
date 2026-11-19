"""Formwork ingest: any file -> CanonicalDocument."""

from formwork_ingest.base import ParsedDocument, Parser, StoredFile, detect_mime
from formwork_ingest.parsers import PARSERS
from formwork_ingest.service import ingest_file, parser_for, validate_upload

__all__ = [
    "PARSERS",
    "ParsedDocument",
    "Parser",
    "StoredFile",
    "detect_mime",
    "ingest_file",
    "parser_for",
    "validate_upload",
]
