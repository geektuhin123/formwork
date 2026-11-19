"""formwork-admin: operator commands that need the schema-owner connection (FORMWORK_DATABASE_ADMIN_URL)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from formwork_core.config import get_settings
from formwork_core.db import admin_engine
from formwork_core.db.repo import create_api_key, create_tenant, revoke_api_key


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = admin_engine(settings)
    pepper = settings.api_key_pepper.get_secret_value()
    try:
        if args.cmd == "create-tenant":
            tenant = await create_tenant(engine, args.name, args.plan, {"retention": args.retention})
            key = await create_api_key(engine, tenant["id"], pepper, "default")
            print(
                json.dumps(
                    {"tenant_id": tenant["id"], "api_key": key, "webhook_secret": tenant["webhook_secret"]},
                    indent=2,
                )
            )
            print("Store the API key now: only its hash is kept.", file=sys.stderr)
        elif args.cmd == "create-key":
            print(
                json.dumps(
                    {
                        "tenant_id": args.tenant_id,
                        "api_key": await create_api_key(engine, args.tenant_id, pepper, args.name),
                    }
                )
            )
        elif args.cmd == "revoke-key":
            await revoke_api_key(engine, args.key_id)
            print(json.dumps({"revoked": args.key_id}))
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="formwork-admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("create-tenant", help="create a tenant and its first API key")
    t.add_argument("name")
    t.add_argument("--plan", default="trial")
    t.add_argument("--retention", choices=["standard", "zero"], default="standard")
    k = sub.add_parser("create-key", help="mint another API key for a tenant")
    k.add_argument("tenant_id")
    k.add_argument("--name", default="default")
    r = sub.add_parser("revoke-key")
    r.add_argument("key_id")
    return asyncio.run(_run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
