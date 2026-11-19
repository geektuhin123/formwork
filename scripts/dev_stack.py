"""No-Docker local stack: throwaway Postgres + migrations + a tenant and API key + API (inline queue, filesystem
object store) + the web console.

    uv run python scripts/dev_stack.py            # prints the URLs and the API key, runs until Ctrl-C

Models: live when ANTHROPIC_API_KEY is set, otherwise the offline FakeProvider. Data lives in a temp directory and is
gone when the script stops. For anything persistent use docker compose.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PEPPER = "dev-stack-pepper"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_http(url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"{url} did not come up")


@dataclass
class Stack:
    api_url: str
    web_url: str
    api_key: str
    tenant_id: str
    procs: list[subprocess.Popen[bytes]] = field(default_factory=list)
    pg: object | None = None

    def stop(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        if self.pg is not None:
            self.pg.stop()  # type: ignore[attr-defined]


def start(
    web: bool = True, api_port: int | None = None, web_port: int | None = None, quiet: bool = True
) -> Stack:
    sys.path.insert(0, str(ROOT))
    from formwork_core.db.repo import create_api_key, create_tenant
    from formwork_testkit.pg import migrate, start_postgres
    from sqlalchemy.ext.asyncio import create_async_engine

    pg = start_postgres("formwork_dev")
    if pg is None:
        raise RuntimeError(
            "no Postgres binaries found (install postgresql + pgvector, or use docker compose)"
        )
    migrate(pg.admin_url, repo_root=str(ROOT))

    async def seed() -> tuple[str, str]:
        engine = create_async_engine(pg.admin_url)
        tenant = await create_tenant(engine, "Dev tenant")
        key = await create_api_key(engine, tenant["id"], PEPPER, "dev")
        await engine.dispose()
        return tenant["id"], key

    tenant_id, key = asyncio.run(seed())
    api_port, web_port = api_port or free_port(), web_port or free_port()
    store = tempfile.mkdtemp(prefix="fw_objects_")
    env = {
        **os.environ,
        "FORMWORK_ENV": "dev",
        "FORMWORK_DATABASE_URL": pg.app_url,
        "FORMWORK_QUEUE": "inline",
        "FORMWORK_OBJECT_STORE": "local",
        "FORMWORK_LOCAL_STORE_PATH": store,
        "FORMWORK_API_KEY_PEPPER": PEPPER,
        "FORMWORK_CORS_ORIGINS": f'["http://127.0.0.1:{web_port}"]',
    }
    out = subprocess.DEVNULL if quiet else None
    stack = Stack(f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}", key, tenant_id, pg=pg)
    stack.procs.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "formwork_api.app:app_factory",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            env=env,
            cwd=ROOT,
            stdout=out,
            stderr=out,
        )
    )
    try:
        wait_http(f"{stack.api_url}/healthz")
        if web:
            web_dir = ROOT / "apps" / "web"
            if not (web_dir / ".next").exists():
                raise RuntimeError("build the web app first: (cd apps/web && npm ci && npm run build)")
            web_env = {
                **os.environ,
                "FORMWORK_API_URL": stack.api_url,
                "FORMWORK_API_KEY": key,
                "PORT": str(web_port),
                "HOSTNAME": "127.0.0.1",
            }
            stack.procs.append(
                subprocess.Popen(
                    ["npx", "next", "start", "-p", str(web_port), "-H", "127.0.0.1"],
                    env=web_env,
                    cwd=web_dir,
                    stdout=out,
                    stderr=out,
                )
            )
            wait_http(stack.web_url)
    except Exception:
        stack.stop()
        raise
    return stack


if __name__ == "__main__":
    s = start(api_port=8000, web_port=3000, quiet=False)
    print(
        f"\nAPI      {s.api_url}  (docs at /docs)\nConsole  {s.web_url}\nAPI key  {s.api_key}\nTenant   {s.tenant_id}\nCtrl-C to stop.\n",
        flush=True,
    )
    try:
        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        s.stop()
