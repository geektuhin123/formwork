from __future__ import annotations

import email
import email.policy
import html
import re
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import ClassVar

from formwork_core.context import JobContext
from formwork_core.errors import FormworkError
from formwork_core.logging import get_logger
from formwork_core.models import BlockType, CanonicalDocument

from formwork_ingest.base import (
    EML,
    MAX_ATTACHMENT_DEPTH,
    MAX_ATTACHMENTS,
    BaseParser,
    ParsedDocument,
    RawBlock,
    StoredFile,
    document_id,
    finalize,
)

log = get_logger("formwork.ingest.email")
_TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)


class EmailParser(BaseParser):
    """Headers and body become blocks; each attachment is parsed recursively as its own child document."""

    mime_types: ClassVar[set[str]] = {EML}

    def __init__(self, depth: int = 0) -> None:
        self.depth = depth

    def parse_sync(self, file: StoredFile, ctx: JobContext) -> ParsedDocument:
        from formwork_ingest.service import parser_for

        msg = email.message_from_bytes(file.data, policy=email.policy.default)
        assert isinstance(msg, EmailMessage)
        doc_id = document_id(ctx, file.sha256)
        headers = [f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Subject", "Date") if msg[h]]
        raw = [RawBlock(BlockType.KEY_VALUE, "\n".join(headers), locator="email.headers")] if headers else []
        body = msg.get_body(preferencelist=("plain", "html"))
        text = ""
        if body is not None:
            text = str(body.get_content())
            if body.get_content_type() == "text/html":
                text = html.unescape(_TAGS.sub(" ", text))
        for i, para in enumerate(p.strip() for p in re.split(r"\n\s*\n", text)):
            if para:
                kind = (
                    BlockType.KEY_VALUE
                    if sum(":" in ln[:45] for ln in para.split("\n")) * 2 >= len(para.split("\n"))
                    else BlockType.TEXT
                )
                raw.append(RawBlock(kind, para, locator=f"email.body#p{i}"))
        meta: dict[str, object] = {"subject": str(msg["Subject"] or "")}
        try:
            if msg["Date"]:
                meta["document_date"] = parsedate_to_datetime(str(msg["Date"])).date().isoformat()
        except (TypeError, ValueError):
            pass
        children: list[ParsedDocument] = []
        if self.depth < MAX_ATTACHMENT_DEPTH:
            for part in list(msg.iter_attachments())[:MAX_ATTACHMENTS]:
                name = part.get_filename() or "attachment"
                payload = part.get_payload(decode=True)
                if not isinstance(payload, bytes) or not payload:
                    continue
                try:
                    child_file = StoredFile(filename=name, data=payload)
                    parser = parser_for(child_file.mime_type, depth=self.depth + 1)
                    child = parser.parse_sync(child_file, ctx)
                except FormworkError as exc:  # an unreadable attachment must not sink the email
                    log.warning("attachment_skipped", filename=name, error=str(exc))
                    continue
                # Re-key the child so the same attachment under two emails stays two documents.
                child_id = document_id(ctx, child_file.sha256, parent=doc_id)
                child = _rekey(child, child_id, doc_id)
                children.append(child)
        doc = CanonicalDocument(
            id=doc_id,
            job_id=ctx.job_id,
            filename=file.filename,
            mime_type=EML,
            sha256=file.sha256,
            page_count=1,
            blocks=finalize(raw, doc_id),
        )
        return ParsedDocument(doc, meta=meta, children=children)


def _rekey(parsed: ParsedDocument, new_id: str, parent_id: str) -> ParsedDocument:
    from formwork_core.ids import stable_id

    old = parsed.document
    blocks = [
        b.model_copy(update={"id": stable_id("blk", new_id, b.reading_order), "document_id": new_id})
        for b in old.blocks
    ]
    parsed.document = old.model_copy(update={"id": new_id, "blocks": blocks})
    parsed.parent_document_id = parent_id
    return parsed
