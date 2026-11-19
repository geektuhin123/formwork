"""uv run ingest <file> --out doc.json"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from formwork_core.context import JobContext
from formwork_core.errors import FormworkError
from formwork_core.provider import FakeProvider
from formwork_core.storage import LocalObjectStore

from formwork_ingest.base import StoredFile
from formwork_ingest.service import ingest_file


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ingest", description="Parse a file into CanonicalDocument JSON.")
    ap.add_argument("file", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="output path (default: stdout)")
    ap.add_argument("--artifacts", type=Path, default=None, help="directory for page images and crops")
    ap.add_argument("--embeddings", action="store_true", help="include block embeddings in the output")
    args = ap.parse_args(argv)
    store = LocalObjectStore(args.artifacts or Path(".formwork/cli-objects"))
    ctx = JobContext(tenant_id="cli", job_id="cli", provider=FakeProvider(), store=store)
    try:
        docs = asyncio.run(ingest_file(StoredFile(filename=args.file.name, data=args.file.read_bytes()), ctx))
    except (FormworkError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    exclude = None if args.embeddings else {"blocks": {"__all__": {"embedding"}}}
    payload = [d.document.model_dump(mode="json", exclude=exclude) for d in docs]
    text = json.dumps(payload[0] if len(payload) == 1 else payload, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text)
        print(
            f"wrote {args.out} ({sum(len(d.document.blocks) for d in docs)} blocks, {len(docs)} document(s))"
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
