# music-recsys

- プロジェクト研究B：音楽推薦システム × LLM × AIエージェント
- 従来型推薦にLLM・Agentを加えることで、何がどの程度改善するか、有効性と限界を定量・定性評価する。
- 研究テーマ・仮説は、先行研究と予備実験から絞り込む。

| 構成 | 方針 |
| --- | --- |
| A：従来型推薦 | Popularity・CF・内容ベース・行列分解・Hybridから選定 |
| B：従来型推薦 + LLM | 嗜好抽出・条件構造化・説明・再ランキングを固定の処理順で実行 |
| C：従来型推薦 + LLM + Agent | 状況に応じて推薦・検索ツールを動的に選択・実行 |

## 現在の状態

- Python 3.12、uv、srcレイアウト。
- 合成データ・Popularity・共通Runner・NDCG / Recall・Notebookを実装済み。
- Music4All-Onion v2の時刻付きイベントを取得・全量集計済み。履歴コホート・全量取り込みと保存済み時系列分割に対応。
- 初期評価はTrainに履歴のある100ユーザー、Popularity、NDCG@10・Recall@10。全量での推薦性能評価は未実施。
- PostgreSQL + pgvectorのローカルDocker環境とAlembicを構築済み。
- MLflowを共通Runnerへ接続し、ローカルサーバーへの条件・指標・成果物・実行状態の記録を確認済み。
- ValidationのGrid探索・成功試行の再利用・設定選択／確認／固定・最終Testを、同じTOMLと保存artifactで管理する。
- LLM・Agent・UIは今後実装。UIより先に検証基盤を整える。

## 文書

| Notebook | 用途 |
| --- | --- |
| [01_data](notebooks/01_data.ipynb) | 保存済みデータのSQL/DataFrame読込み・検証・EDA・分割状態の確認 |
| [02_analysis](notebooks/02_analysis.ipynb) | 検証の中心：探索結果、設定選択、K別・ユーザー別分析、Testの最終比較 |
| [03_recommendation](notebooks/03_recommendation.ipynb) | 選択設定を引継ぎ、Validationの履歴・推薦・根拠・特徴・ローカル音源を確認 |

- 3冊とも独立実行可能。既定は合成データと参考EDA。01のDB操作は読込みのみ。
- 02・03・CLIは`MUSIC_RECSYS_EXPERIMENT_CONFIG`で同じTOMLを読む。モデル・パラメータを03へ再入力しない。02の既定操作は読込みで、明示した選択・固定だけを共通APIへ委譲する。
- **01で確認 → CLI Smoke → Validation探索 → 02で選択 → 03で確認 → 固定 → CLI最終Test → 02で比較**。[検証フロー](docs/experiment-workflow.md) / [既存結果の分析](docs/result-analysis.md)

## 合成データで検証フローを試す

同じ設定ファイルを各コマンドで使う。Popularityには探索可能なパラメータがないため、この例はseed反復と導線の確認。1変数／2変数の探索基盤は合成モデルのテストで検証する。

```bash
uv run --locked --group data --group tracking python -m music_recsys.experiment_search --config configs/workflow-toy.toml smoke
uv run --locked --group data --group tracking python -m music_recsys.experiment_search --config configs/workflow-toy.toml search
uv run --locked --group data --group tracking python -m music_recsys.experiment_search --config configs/workflow-toy.toml select
```

次に03で推薦を確認し、同じTOMLに対して`review --accept --note "確認内容"`、`freeze`、`test`の順に実行する。各コマンドは状態・次の操作を表示する。Testは固定済み設定だけで実行し、探索には使用しない。

- 設定・主指標は実験開始前に指定する。未指定なら自動選択・Testを止める。
- 成功試行は内容hashを検証して再利用する。失敗・予算切れは`search`で再開できる。データ・コード・主指標が変わった場合は別の出力先で開始する。
- 出力はGit管理外の`data/experiments/`。実験結果の未測定値は0で補わない。

| 文書 | 内容 |
| --- | --- |
| [CONTRIBUTING](CONTRIBUTING.md) | 環境・実行・ブランチ・コミットのルール |
| [研究計画](docs/research-plan.md) | 背景・課題・比較・進め方 |
| [評価方針](docs/evaluation-protocol.md) | 共通基盤・形式・指標候補 |
| [検証・探索フロー](docs/experiment-workflow.md) | TOML、Stage A〜D、状態、選択artifact、Notebook間の移動、最終Test |
| [データセット方針](docs/dataset-investigation.md) | Music4All / TalkPlayの用途・分割 |
| [DB・取り込み設計](docs/db-design.md) | 保存先・ID・スナップショット・初回取り込みの合意事項 |
| [テーブル定義](docs/db-schema.md) | カラム・キー・参照整合性・不変性とmanifest形式 |
| [ローカルDB](docs/local-db.md) | Docker Compose・環境変数・Alembicの手順 |
| [取り込み元確認](docs/music4all-import-sources.md) | Music4Allの実配布ファイル・カラム・取得状況 |
| [Music4All取り込み](docs/music4all-import.md) | メタデータ・履歴コホート・全量の取り込みとsnapshot読込み |
| [Music4All Benchmark](docs/music4all-benchmark.md) | 保存済み分割・Benchmark・Notebookの実行 |
| [MLflow](docs/mlflow.md) | 共通実験管理の設定・実行 |
| [先行研究](docs/papers/README.md) | 調査候補と記録テンプレート |
| [データ取得](data/README.md) | 出典・照合・加工コマンド |
| [Music4All調査結果](experiments/music4all/summary.md) | 分割前の全量集計・時刻品質 |
| [Music4All初期評価](experiments/music4all/smoke.md) | 100ユーザーの動作確認結果・限界 |
