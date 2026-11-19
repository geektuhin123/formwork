"""Alembic environment. Runs as the schema owner: FORMWORK_DATABASE_ADMIN_URL (falls back to FORMWORK_DATABASE_URL)."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _url() -> str:
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("FORMWORK_DATABASE_ADMIN_URL")
        or os.environ.get("FORMWORK_DATABASE_URL")
    )
    if not url:
        raise RuntimeError("set FORMWORK_DATABASE_ADMIN_URL (or FORMWORK_DATABASE_URL) to run migrations")
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
