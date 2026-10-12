# 共通の実験管理

## 決定事項

- 検証基盤では、MLflowを従来推薦・LLM / Agentの共通の実験管理基盤にする。
- LangChain・LangGraphの採用判断は、LLM / Agentの実装時に行う。今回の依存には追加していない。

## 導入済みの構成

| 項目 | 実装 |
| --- | --- |
| MLflow | [3.17.0](https://pypi.org/project/mlflow/3.17.0/)。`tracking`依存groupと`uv.lock`で固定 |
| 実験メタデータ | 既存PostgreSQL内の専用DB `music_recsys_mlflow` |
| DBの変更 | MLflow自身が専用DBを管理。推薦用DBのAlembicとは分離 |
| 成果物 | Git管理外の`data/mlflow/artifacts/`。サーバー経由で送受信 |
| ローカルUI / API | `http://127.0.0.1:5000`。localhostのみ公開 |
| Runner / CLI | `run_experiment`・`run_benchmark`から同じ記録処理を呼ぶ |
| 03 Notebook | 保存済み集計・詳細結果を読み込んで比較する |
| 将来のモデル実装 | `ExperimentRun`で条件・集計指標・費用・成果物を記録可能 |

- 元データ・ユーザーの生履歴・要求別の推薦結果はMLflowへ自動送信しない。MLflowの成果物は集計した`report.json`と`summary.md`。
- CLIの`--output`でローカル詳細を保存し、MLflowを使う場合は`reports/details.json`に相対パス・SHA-256を記録する。詳細自体は送信しない。
- ローカル`results.json`にはrun ID・experiment ID・statusも残す。manifest記録失敗時はローカル結果を残して`TrackingError`を通知する。
- seed、モデル、top-k、データ・Train・分割・コード・設定のSHA-256、Python・パッケージ版を記録する。
- NDCG@K・Recall@K、件数・失敗数・実行時間を記録する。未定義の指標は0に変換しない。
- 推薦の失敗を含むrunは`FAILED`。記録処理の失敗は`TrackingError`で通知し、成功扱いしない。接続断でstatus更新も失敗した場合は、MLflow側に`RUNNING`が残ることがある。
- 推薦用DBの`experiment_runs`との対応付けは未実装。

## 起動・実行

- Docker Desktopと[ローカルPostgreSQL](local-db.md)を起動し、Git管理外の`.env`にDB接続設定を用意する。
- 認証情報は環境変数で渡し、コマンド引数・設定ファイル・成果物に書かない。

```bash
uv sync --locked --group data --group notebook --group tracking
docker compose up -d --wait
uv run --locked --env-file .env --group tracking music-recsys-mlflow init-db
uv run --locked --env-file .env --group tracking music-recsys-mlflow serve
```

- `init-db`は専用DBがなければ作成し、存在する場合はそのまま使う。推薦用DBと同じ名前は拒否する。
- `serve`は前景で起動する。別のターミナルから次を実行する。終了は`Ctrl-C`。

```bash
curl --fail http://127.0.0.1:5000/health
uv run --locked --group tracking music-recsys-benchmark \
  --config configs/toy-mlflow.toml --output data/mlflow/runs
```

| 設定 | 値・優先順位 |
| --- | --- |
| 記録の有効化 | `[tracking].enabled`。`MUSIC_RECSYS_TRACKING=true/false`で上書き。CLIの`--tracking` / `--no-tracking`が最優先 |
| 接続先 | `MLFLOW_TRACKING_URI` → `[tracking].tracking_uri` → `http://127.0.0.1:5000` |
| 実験名 | `MLFLOW_EXPERIMENT_NAME` → `[tracking].experiment_name` → `music-recsys` |
| run名 | `MLFLOW_RUN_NAME` → `[tracking].run_name` |
| サーバー専用DB | `MLFLOW_DATABASE`。既定`music_recsys_mlflow` |
| サーバーのポート | `MLFLOW_PORT`。既定5000。変更時はクライアント接続先も変更 |
| サーバーの成果物保存先 | `MLFLOW_ARTIFACT_ROOT`。既定`data/mlflow/artifacts` |

- `configs/toy.toml`は従来どおり記録なしで実行できる。`--tracking`または環境変数でも有効化できる。
- 研究室サーバーを使う場合は`MLFLOW_TRACKING_URI`を変更する。サーバー側の配置・公開・認証設定は未実装。URIに認証情報やtokenを埋め込まない。[MLflow公式のサーバー設定](https://mlflow.org/docs/latest/tracking/server/)

## Pythonからの実行

```python
from pathlib import Path
from music_recsys.evaluation.benchmark import run_experiment, save_report
from music_recsys.evaluation.tracking import TrackingConfig

tracking = TrackingConfig.from_settings({"enabled": True})
report = run_experiment(Path("configs/toy.toml"), tracking=tracking)
save_report(report, Path("data/mlflow/runs"), tracking=tracking)
```

- 独自の推薦関数には`run_benchmark(..., tracking=...)`を使う。
- Runnerの外で学習やLLM / Agentを記録する場合は、同じ接続設定で次のcontextを使える。条件には版・snapshot・分割・モデル・前処理の識別情報、指標には集計値を渡す。

```python
from music_recsys.evaluation.tracking import ExperimentRun

with ExperimentRun(
    TrackingConfig.from_settings({"enabled": True}),
    params={"model_name": "example-model", "seed": 42},
) as run:
    # モデルの学習・評価を実行し、実測した集計値を渡す。
    run.log_metrics({"cost_usd": 0.002, "input_tokens": 10})
```

- context内で例外が発生するとrunを`FAILED`にする。例外の型だけを記録し、本文は送信しない。
- `run.log_params(...)`・`run.log_artifact(...)`も利用できる。認証情報・生データを渡さない。

## 03 Notebookでの読込み

- `MLFLOW_RUNS`に表示名とrun IDを指定する。集計成果物だけでも平均・件数を比較できる。
- ユーザー別分析・信頼区間には詳細が必要。`DETAILS_ROOT`にCLIの`--output`で使ったディレクトリを指定する。
- manifestのSHA-256・run ID・集計との一致を検証し、対応するローカル詳細を読む。[保存結果の分析](result-analysis.md)

## 検証結果・コマンド

- 2026-10-12：MLflow 3.17.0 / PyArrow 25.0.1、ローカルPostgreSQL専用DBとHTTPサーバーを確認。
- 合成データで13条件・10指標・成果物2件の記録とダウンロード、`FINISHED`を確認。NDCG@2は0.561019236584229、Recall@2は0.625。
- 共通contextで`cost_usd`・`input_tokens`と例外時の`FAILED`を確認。専用DBに各statusが保存され、推薦用DBのAlembic revisionは`0002`を維持。
- 自動テストは実MLflowと一時SQLite DBを使用。通常のテストではPostgreSQLや常駐サーバーを必要としない。

```bash
uv run --locked --group data --group notebook --group tracking pytest
uv run --locked ruff check src tests notebooks migrations
uv run --locked ruff format --check src tests notebooks migrations
```

- `tracking` groupなしの場合、MLflow実記録のテストは読み飛ばす。
- 同じ仮想環境からNotebookを起動するときも`--group tracking`を付ける。起動済みサーバーと並行してコマンドを実行する場合は、上記の`uv sync`後に`uv run --locked --no-sync ...`を使えば依存の削除を防げる。
