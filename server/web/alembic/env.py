"""Alembic environment — SQLite URL from MEETINGBOX_DB_PATH (server/web/.env)."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from alembic import context
from sqlalchemy import create_engine, pool

from database import DB_PATH  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _sqlite_url() -> str:
    p = Path(DB_PATH).resolve()
    return f"sqlite:///{p.as_posix()}"


def run_migrations_offline() -> None:
    context.configure(
        url=_sqlite_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_sqlite_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
