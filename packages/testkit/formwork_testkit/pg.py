"""A throwaway Postgres for tests.

Order of preference: FORMWORK_TEST_DATABASE_ADMIN_URL (CI service container) -> local `initdb`/`pg_ctl` binaries
(a temp cluster on a free port; started as the `postgres` OS user when the tests run as root). Returns None when
neither is available so DB tests skip rather than fail.
"""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass

APP_ROLE = "formwork_app"
APP_PASSWORD = "formwork_app"


@dataclass
class TempPostgres:
    admin_url: str
    app_url: str
    _datadir: str | None = None
    _bindir: str | None = None
    _as_user: str | None = None

    def stop(self) -> None:
        if self._datadir and self._bindir:
            _run(
                [f"{self._bindir}/pg_ctl", "-D", self._datadir, "-m", "immediate", "stop"],
                self._as_user,
                check=False,
            )
            shutil.rmtree(os.path.dirname(self._datadir), ignore_errors=True)


def _run(cmd: list[str], as_user: str | None, check: bool = True) -> subprocess.CompletedProcess[str]:
    if as_user:
        cmd = ["runuser", "-u", as_user, "--", *cmd]
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _bindir() -> str | None:
    found = shutil.which("initdb")
    if found:
        return os.path.dirname(found)
    cands = sorted(glob.glob("/usr/lib/postgresql/*/bin/initdb"), reverse=True)
    return os.path.dirname(cands[0]) if cands else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def start_postgres(dbname: str = "formwork_test") -> TempPostgres | None:
    admin = os.environ.get("FORMWORK_TEST_DATABASE_ADMIN_URL")
    if admin:
        app = os.environ.get("FORMWORK_TEST_DATABASE_URL") or admin
        return TempPostgres(admin_url=admin, app_url=app)
    bindir = _bindir()
    if not bindir:
        return None
    as_user = None
    if os.geteuid() == 0:
        if not shutil.which("runuser"):
            return None
        as_user = "postgres"
    base = tempfile.mkdtemp(prefix="fwpg_")
    datadir = os.path.join(base, "data")
    if as_user:
        shutil.chown(base, user=as_user)
        os.chmod(base, 0o755)
    port = _free_port()
    try:
        _run([f"{bindir}/initdb", "-D", datadir, "-U", "postgres", "-A", "trust", "-E", "UTF8"], as_user)
        _run(
            [
                f"{bindir}/pg_ctl",
                "-D",
                datadir,
                "-w",
                "-o",
                f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={base} -c fsync=off -c max_connections=100",
                "-l",
                os.path.join(base, "log"),
                "start",
            ],
            as_user,
        )
        psql = [
            f"{bindir}/psql",
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
        ]
        _run([*psql, "-c", f"CREATE DATABASE {dbname}"], None)
        _run([*psql, "-c", f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'"], None)
    except (subprocess.CalledProcessError, OSError):
        shutil.rmtree(base, ignore_errors=True)
        return None
    return TempPostgres(
        admin_url=f"postgresql+psycopg://postgres@127.0.0.1:{port}/{dbname}",
        app_url=f"postgresql+psycopg://{APP_ROLE}:{APP_PASSWORD}@127.0.0.1:{port}/{dbname}",
        _datadir=datadir,
        _bindir=bindir,
        _as_user=as_user,
    )


def migrate(admin_url: str, revision: str = "head", repo_root: str | None = None) -> None:
    from alembic import command
    from alembic.config import Config

    root = repo_root or os.environ.get("FORMWORK_REPO_ROOT") or os.getcwd()
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    cfg.set_main_option("sqlalchemy.url", admin_url)
    if revision == "base":
        command.downgrade(cfg, "base")
    else:
        command.upgrade(cfg, revision)
