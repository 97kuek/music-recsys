# Music4Allの分割・検証手順

- 前提：[取り込み](music4all-import.md)の`history`または`full` snapshot、Alembic `0003`。
- [評価条件](evaluation-protocol.md#music4allの初回実データ検証)、[実行結果](../experiments/music4all/smoke.md)。
- `SNAPSHOT_ID`・`SPLIT_ID`は各CLIの戻り値を指定する。値を暗黙に最新へ切り替えない。
- 以下は依存を導入済みの仮想環境で、`.env`を読み込んで実行する。

```bash
uv sync --locked --group db --group data --group notebook --group tracking
uv run --locked --no-sync --env-file .env python -m music_recsys.temporal_cli \
  prepare --snapshot-id "$SNAPSHOT_ID"
uv run --locked --no-sync --env-file .env python -m music_recsys.temporal_cli \
  export --split-id "$SPLIT_ID" --phase validation \
  --output data/benchmarks/music4all-smoke/final/validation.json
```

| 処理 | 記録・検証 |
| --- | --- |
| 分割 | 元履歴・Train・Validation・Testの不変Parquet。snapshotとは別の`benchmark_splits`で管理 |
| 公開前 | snapshotの履歴出典、SHA-256、件数、ID・型、時刻境界、重複数を含む全件の一致 |
| 分割統計 | 件数・期間・Cold Start・既聴・正解・評価可能ユーザー数 |
| 出典 | 配布版・snapshot・split ID、抽出条件、前処理コード版、依存lock SHA |
| 実験 | 学習・候補・正解のhash、モデル・seed・K、実行時間、MLflow run ID |

- 大量イベントはParquetで処理する。小規模Runner用JSONの書出しは学習ユーザー2,000人・学習イベント500万件まで。全量取り込み・分割と、全量モデル評価を混同しない。
- export先は新規ファイル。再実験で既存入力・結果を上書きしない。

## Validation・再現性

```bash
uv run --locked --no-sync --env-file .env music-recsys-benchmark \
  --config configs/music4all-smoke-validation.toml --output data/benchmarks/music4all-smoke/runs
uv run --locked --no-sync --env-file .env python -m music_recsys.validation_study \
  --dataset data/benchmarks/music4all-smoke/final/validation.json \
  --output data/benchmarks/music4all-smoke/reproducibility --seeds 0 42
```

- seed 0・42に加え、最後のseedを再実行する。時間を除いた推薦結果・要求別指標のhashを照合する。
- 成功条件は、出力一致・全runの推薦失敗0・評価対象あり。Popularityには乱数を使う処理がない。
- 人数・抽出seedの確認は`music-recsys-import --selection history --users 100`／`1000`と`--selection-seed 0`／`42`で4組のsnapshotを作り、それぞれprepare→Validation export→上記検証を実行する。
- 異なる抽出データは条件が異なるため、02の同条件モデル比較には混ぜない。

## Test・Notebook

- Validationの確認後、固定条件でTest入力を生成する。TestはTrain＋Validationで学習する。

```bash
uv run --locked --no-sync --env-file .env python -m music_recsys.temporal_cli \
  export --split-id "$SPLIT_ID" --phase test --output data/benchmarks/music4all-smoke/final/test.json
uv run --locked --no-sync --env-file .env music-recsys-benchmark \
  --config configs/music4all-smoke-test.toml --output data/benchmarks/music4all-smoke/runs
```

| Notebook | 環境変数 |
| --- | --- |
| 01 | `MUSIC_RECSYS_SNAPSHOT_ID`、`MUSIC_RECSYS_SPLIT_ID`。保存済みデータだけを読む |
| 02_analysis | `MUSIC_RECSYS_EXPERIMENT_CONFIG`に探索TOML。旧保存結果は`MUSIC_RECSYS_REPORT_PATH`。Validation・Testを別々に分析 |
| 03_recommendation | 同じ`MUSIC_RECSYS_EXPERIMENT_CONFIG`から選択設定を取得。任意で`MUSIC_RECSYS_REQUEST_ID`。事前確認はValidationのみ |

- `uv run --locked --no-sync --env-file .env jupyter lab`で起動。環境変数未指定では従来の合成データ。
- 実行済みNotebook・履歴・推薦結果は`data/`配下に保存し、Gitには含めない。
- [MLflow起動](mlflow.md)：今回5000番はmacOSが使用中のため、`MLFLOW_PORT=5001`で起動し、クライアントに`MLFLOW_TRACKING_URI=http://127.0.0.1:5001`を指定した。

## 全量向けの回帰検証

```bash
MUSIC_RECSYS_TEST_DB=1 MUSIC_RECSYS_TEST_SCALE=1 \
  uv run --locked --no-sync --env-file .env pytest
```

- 通常のテストは600万件の回帰ケースを省略する。`MUSIC_RECSYS_TEST_SCALE=1`で128MB上限の全件照合を有効にする。
- 全量処理は2GBを上限とし、外部ソート・小さなArrow batchで重複数を含む全列値を比較する。失敗した分割は公開しない。
