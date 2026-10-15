# 任意のDB機能

研究室の標準経路はファイル保存のみで、以下はDBを利用できる環境向けの実装
Docker・DBの利用権限を持つ環境だけで実行する

## 構成

| 要素 | 実装 |
| --- | --- |
| DB | compose.yamlで固定したPostgreSQL + pgvector |
| 接続 | .env.exampleの変数、Pythonはengine_from_environment |
| スキーマ | Alembic、migrations/sqlとmigrations/versionsが定義元 |
| 取り込み | adapters/music4all.py・ingestion/pipeline.py |
| 保存 | メタデータと台帳はDB、履歴・分割はParquet |
| 識別 | 内部UUIDとデータセット固有の元IDを分離 |

## 台帳と不変性

- datasets / dataset_versionsで配布版を識別
- tracks / usersとメタデータ本体を分け、snapshotの構成を固定
- import_runsに入力fingerprint・実行状態・失敗を記録
- data_artifactsにhash・保存キー・行数を記録
- 公開時に入力・構成・Parquet内容を検証し、1トランザクションで確定
- 公開済みsnapshotとbenchmark_splitsは更新・削除を拒否
- 同じ入力は保存物と内部IDを再利用、異なる内容の重複は拒否
- 複合FK・トリガー・manifest形式の詳細はSQL定義を参照

## 起動

.env.exampleを.envへコピーし、パスワードをローカルで設定する

```bash
docker compose up -d --wait
uv run --locked --env-file .env --group db alembic upgrade head
uv run --locked --env-file .env --group db alembic current
```

## 履歴の取り込み

取得済み元ファイル・events.parquet・変換manifestが必要

```bash
uv run --locked --env-file .env --group db --group data music-recsys-import   --record data/raw/music4all-onion-v2/record.json   --tags data/raw/music4all-onion-v2/id_tags_dict.tsv.bz2   --events data/raw/music4all-onion-v2/userid_trackid_timestamp.tsv.bz2   --event-cache data/processed/music4all-onion-v2/events.parquet   --version-label v2 --selection history --users 100   --train-end 2017-03-29T00:00:00 --storage-root data/imports --lockfile uv.lock
```

| selection | 対象 |
| --- | --- |
| metadata | 独立に選んだ楽曲・ユーザー、履歴は登録しない |
| history | Train観測ユーザーの全期間履歴、任意のselection-seedに対応 |
| full | 全イベント・全ユーザー・公開タグ内の全曲 |

## 分割とexport

SNAPSHOT_IDとSPLIT_IDには各コマンドの戻り値を明示する

```bash
uv run --no-sync --env-file .env python -m music_recsys.temporal_cli prepare --snapshot-id "$SNAPSHOT_ID"
uv run --no-sync --env-file .env python -m music_recsys.temporal_cli export   --split-id "$SPLIT_ID" --phase validation --protocol listening-v2   --output data/benchmarks/db-study/validation
uv run --no-sync --env-file .env python -m music_recsys.features   --snapshot-id "$SNAPSHOT_ID" --output data/benchmarks/db-study/features.json
```

- Testは同じsplitからphase=test・別出力先で生成
- NotebookはDBへ接続しない / export済みv2 bundleをTOMLへ指定して読み込む
- ファイル経路のfile-識別子をDB用UUIDとして渡さない
- DBテストは[開発手順](../CONTRIBUTING.md)、通常運用は[研究室の運用](lab-operation.md)
