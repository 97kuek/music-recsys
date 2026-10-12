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
- revision `0001`はpgvectorの固定版有効化。`0002`は[初回13テーブルと制約](db-schema.md)。

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

## DBテスト

```bash
MUSIC_RECSYS_TEST_DB=1 uv run --locked --env-file .env --group db --group data --group notebook --group tracking pytest
```

- 明示的に有効化したときだけDBへ接続する。通常のpytestではDBテストを読み飛ばす。
- テストごとに`test_recsys_`で始まるランダム名の専用schemaを作成し、終了時に削除する。
- Alembicの履歴テーブルも専用schemaに置く。`public`の履歴は変更しない。
- downgrade / upgradeの往復検証は専用schema内だけで実行する。
- IDの一意性・所属・出典manifest照合・hash・入力封印・公開COMMIT・失敗ROLLBACK・再実行・同時公開・変更拒否と、[Adapter・Parquet保存](music4all-import.md)を検証する。

## 検証結果

- 2026-10-12：Docker Desktop起動後、Composeの起動・ヘルスチェックを確認。
- 認証付き接続、Alembic適用・再適用、PostgreSQL 18.6 / pgvector 0.8.7 / revision `0001`を確認。
- 一時テーブルでベクトルのコサイン類似度検索を確認し、検証データはロールバック。
- コンテナを再作成し、同じVolumeでpgvectorとmigration履歴が保持されることを確認。
- DB接続なしのupgrade / downgrade SQL生成、既存56件のテスト、Ruff、ビルドを確認。
- 検証用`.env`はGit管理外。実際のパスワードは記録・表示していない。
- 2026-10-12：revision `0002`をローカルDBへ適用・再適用し、13テーブルと`0002 (head)`を確認。
- DB60件・既存56件の計116件、Ruff、upgrade / downgrade SQL生成、ビルドを確認。公開の原子性・再実行・ロック待ちを含む同時公開・変更拒否を検証。
- 検証用schemaの残存は0。テスト用の行は通常DBに残していない。
- 2026-10-12：Music4Allの楽曲・ユーザー各100件を取り込み、再実行で同じsnapshotを再利用。成功runは2件。[取り込み結果](music4all-import.md#実データでの検証結果)
