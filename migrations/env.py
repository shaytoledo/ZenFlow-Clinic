"""Alembic environment (Phase 12.2.3). The target schema is `zenflow.schema.metadata`."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from zenflow.migrate import compare_default
from zenflow.schema import metadata

config = context.config
target_metadata = metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from zenflow.migrate import database_url

    return database_url()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # zenflow.migrate hands over an open connection (so SQLite sees the app's own file and WAL)
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        _run(conn)


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_server_default=compare_default,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
