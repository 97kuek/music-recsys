# ローカルDB

## 構成

| 項目 | 設定 |
| --- | --- |
| PostgreSQL | 18.6。固定イメージの両アーキテクチャの設定とローカル実DBで確認 |
| pgvector | 0.8.7 |
| イメージ | [compose.yaml](../compose.yaml)のタグ＋digestで固定。arm64 / amd64に対応 |
| 永続化 | Docker Volume `postgres_data`を`/var/lib/postgresql`へマウント |
| 接続情報 | Git管理外の`.env`または環境変数。変数名は[.env.example](../.env.example) |
| スキーマ管理 | [alembic.ini](../alembic.ini)、`migrations/versions/` |

- PostgreSQL 18のVolume配置は[公式イメージ仕様](https://hub.docker.com/_/postgres)、pgvectorは[公式イメージ](https://github.com/pgvector/pgvector#docker)を参照。
- 初期migrationはpgvectorの固定版有効化。楽曲・ユーザー・スナップショットのテーブルは設計確定後にmigrationで追加する。

## 起動・スキーマ適用

- Docker Desktopを起動する。
- `.env`がない場合は`.env.example`をコピーし、`POSTGRES_PASSWORD`をローカルで設定する。実際の認証情報はGitに含めない。
- Composeは`.env`を読み、Python側は`uv run --env-file .env`で同じ接続設定を読む。

```bash
uv sync --locked --group db
docker compose config --quiet
docker compose up -d --wait
uv run --locked --env-file .env --group db alembic upgrade head
uv run --locked --env-file .env --group db alembic current
docker compose ps
```

- `docker compose down`で停止・コンテナ削除しても、名前付きVolumeは保持される。

## Migration

| 用途 | コマンド |
| --- | --- |
| DBへ適用 | `uv run --locked --env-file .env --group db alembic upgrade head` |
| 適用済みの版 | `uv run --locked --env-file .env --group db alembic current` |
| 新規revision作成 | `uv run --locked --group db alembic revision -m "変更内容"` |
| DB接続なしでSQL確認 | `uv run --locked --group db alembic upgrade head --sql` |

- 変更はrevisionファイルに記録し、Alembicを通して適用する。
- ORMモデルは未導入。現在は明示的なmigrationを記述する。[Alembic公式](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- 研究室サーバーでは環境変数の接続先を変更し、同じrevisionを使用する。Dockerを使う場合は同じ固定イメージを利用する。

## 検証結果

- 2026-10-12：Docker Desktop起動後、Composeの起動・ヘルスチェックを確認。
- 認証付き接続、Alembic適用・再適用、PostgreSQL 18.6 / pgvector 0.8.7 / revision `0001`を確認。
- 一時テーブルでベクトルのコサイン類似度検索を確認し、検証データはロールバック。
- コンテナを再作成し、同じVolumeでpgvectorとmigration履歴が保持されることを確認。
- DB接続なしのupgrade / downgrade SQL生成、既存56件のテスト、Ruff、ビルドを確認。
- 検証用`.env`はGit管理外。実際のパスワードは記録・表示していない。
