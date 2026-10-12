"""実DBテストは明示的に有効化し、テスト専用schema内だけで実行する。"""

import os
from uuid import uuid4

import pytest

if os.environ.get("MUSIC_RECSYS_TEST_DB") != "1":
    pytest.skip("set MUSIC_RECSYS_TEST_DB=1 to run PostgreSQL tests", allow_module_level=True)

pytest.importorskip("sqlalchemy", reason="install the db dependency group")
pytest.importorskip("alembic", reason="install the db dependency group")

from alembic import command
from db_support import migration_config
from sqlalchemy import URL, create_engine
from sqlalchemy.exc import OperationalError


@pytest.fixture
def db():
    url = URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    )
    admin = create_engine(url, hide_parameters=True)
    schema = "test_recsys_" + uuid4().hex
    try:
        with admin.begin() as connection:
            public_version_exists = connection.exec_driver_sql(
                "SELECT to_regclass('public.alembic_version') IS NOT NULL"
            ).scalar_one()
            public_versions = (
                connection.exec_driver_sql("SELECT version_num FROM public.alembic_version")
                .scalars()
                .all()
                if public_version_exists
                else []
            )
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    except OperationalError:
        admin.dispose()
        pytest.fail("Cannot connect to the PostgreSQL test environment", pytrace=False)
    engine = create_engine(
        url,
        hide_parameters=True,
        connect_args={"options": f"-c search_path={schema},public -c statement_timeout=10000"},
    )
    try:
        with engine.begin() as connection:
            command.upgrade(migration_config(connection), "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            if public_version_exists:
                assert (
                    connection.exec_driver_sql("SELECT version_num FROM public.alembic_version")
                    .scalars()
                    .all()
                    == public_versions
                ), "Test migrations changed the public revision"
        admin.dispose()
