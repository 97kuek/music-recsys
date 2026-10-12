"""DBテストのmigration設定。conftestのモジュール名に依存しない。"""

from pathlib import Path

from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]


def migration_config(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["connection"] = connection
    return config
