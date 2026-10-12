"""環境変数で接続し、offlineでは接続情報なしでSQLを生成する。"""

import os
from logging.config import fileConfig

from alembic import context
from alembic.util import CommandError
from sqlalchemy import URL, create_engine, pool
from sqlalchemy.exc import OperationalError

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# ドメインのテーブル設計は別途確定し、明示的なmigrationで追加する。
target_metadata = None


def database_url() -> URL:
    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise CommandError("Missing required environment variables: " + ", ".join(missing))
    try:
        port = int(os.environ.get("POSTGRES_PORT", "5432"))
    except ValueError:
        raise CommandError("POSTGRES_PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise CommandError("POSTGRES_PORT must be an integer between 1 and 65535")
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=port,
        database=os.environ["POSTGRES_DB"],
    )


def run_migrations_offline() -> None:
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    except OperationalError:
        raise CommandError(
            "Database operation failed; check connection settings, server availability and access"
        ) from None
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
