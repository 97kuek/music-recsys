"""既存PostgreSQLの専用DBを使うローカルMLflowサーバー。"""

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    domain_database: str
    database: str
    user: str
    password: str = field(repr=False)
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    port: int = 5000
    artifact_root: Path = Path("data/mlflow/artifacts")

    @classmethod
    def from_env(cls) -> "ServerConfig":
        required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
        if any(not os.environ.get(name) for name in required):
            raise ValueError("POSTGRES_DB, POSTGRES_USER and POSTGRES_PASSWORD must be configured")
        database = os.environ.get("MLFLOW_DATABASE", "music_recsys_mlflow")
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", database):
            raise ValueError("MLFLOW_DATABASE must be a PostgreSQL identifier")
        if database in {os.environ["POSTGRES_DB"], "postgres", "template0", "template1"}:
            raise ValueError("MLflow must use a separate database from the domain database")
        postgres_port = int(os.environ.get("POSTGRES_PORT", "5432"))
        port = int(os.environ.get("MLFLOW_PORT", "5000"))
        if not (1 <= postgres_port <= 65535 and 1 <= port <= 65535):
            raise ValueError("database and server ports must be between 1 and 65535")
        return cls(
            domain_database=os.environ["POSTGRES_DB"],
            database=database,
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            postgres_host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            postgres_port=postgres_port,
            port=port,
            artifact_root=Path(
                os.environ.get("MLFLOW_ARTIFACT_ROOT", "data/mlflow/artifacts")
            ).resolve(),
        )

    def server_env(self) -> dict[str, str]:
        from sqlalchemy import URL

        env = dict(os.environ)
        env["MLFLOW_BACKEND_STORE_URI"] = URL.create(
            "postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.database,
        ).render_as_string(hide_password=False)
        env["MLFLOW_REGISTRY_STORE_URI"] = env["MLFLOW_BACKEND_STORE_URI"]
        env["MLFLOW_DISABLE_AGENT_HINT"] = "1"
        env["MLFLOW_ENABLE_TELEMETRY"] = "false"
        return env

    def command(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "mlflow",
            "server",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--workers",
            "1",
            "--serve-artifacts",
            "--artifacts-destination",
            str(self.artifact_root),
        ]


def initialize_database(config: ServerConfig) -> None:
    """専用DBがない場合だけ作成する。ドメインDBと既存DBの内容は変更しない。"""
    import psycopg
    from psycopg import sql

    with psycopg.connect(
        dbname="postgres",
        user=config.user,
        password=config.password,
        host=config.postgres_host,
        port=config.postgres_port,
        autocommit=True,
        connect_timeout=10,
    ) as connection:
        if (
            connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (config.database,)
            ).fetchone()
            is None
        ):
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config.database))
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MLflow with a dedicated PostgreSQL database")
    parser.add_argument("action", choices=("init-db", "serve"))
    args = parser.parse_args()
    try:
        config = ServerConfig.from_env()
        if args.action == "init-db":
            initialize_database(config)
            print(f"MLflow database ready: {config.database}")
            return
        config.artifact_root.mkdir(parents=True, exist_ok=True)
        print(f"MLflow: http://127.0.0.1:{config.port} (database: {config.database})", flush=True)
        # 接続URIや例外本文を端末へ流さず、終了コードで起動失敗を知らせる。
        result = subprocess.run(
            config.command(),
            env=config.server_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode:
            parser.exit(
                1, "MLflow server stopped unsuccessfully; check database and port settings\n"
            )
    except KeyboardInterrupt:
        pass
    except (ValueError, ImportError) as error:
        if isinstance(error, ImportError):
            parser.error("install the tracking dependency group")
        parser.error(str(error))
    except Exception as error:
        parser.error(f"MLflow setup failed ({type(error).__name__}); check database settings")


if __name__ == "__main__":
    main()
