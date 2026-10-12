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
- Music4All-Onion v2の時刻付きイベントを取得・全量集計済み。分割条件は未確定。
- 公開Music4Allの100曲・100ユーザーをDBとParquetへ取り込み、再実行で同じsnapshotを再利用できることを確認済み。
- PostgreSQL + pgvectorのローカルDocker環境とAlembicを構築済み。
- MLflowを共通Runnerへ接続し、ローカルサーバーへの条件・指標・成果物・実行状態の記録を確認済み。
- LLM・Agent・UIは今後実装。UIより先に検証基盤を整える。

## 文書

| Notebook | 用途 |
| --- | --- |
| [01_data](notebooks/01_data.ipynb) | 保存済みデータのSQL/DataFrame読込み・検証・EDA・分割状態の確認 |
| [02_recommendation](notebooks/02_recommendation.ipynb) | 推薦モデルの動作確認・推薦結果とタグの分析 |
| [03_analysis](notebooks/03_analysis.ipynb) | 指標の可視化・同条件の保存結果/MLflow成果物の比較・考察 |

- 3冊とも独立実行可能。既定では保存済み合成データとEDAを使い、DB・常駐MLflowは不要。01は公開snapshot指定で読み取り専用SQLを使う。
- データ処理・学習・評価は`src/`の共通APIを呼び、Notebookは分析・可視化に使う。

| 文書 | 内容 |
| --- | --- |
| [CONTRIBUTING](CONTRIBUTING.md) | 環境・実行・ブランチ・コミットのルール |
| [研究計画](docs/research-plan.md) | 背景・課題・比較・進め方 |
| [評価方針](docs/evaluation-protocol.md) | 共通基盤・形式・指標候補 |
| [データセット方針](docs/dataset-investigation.md) | Music4All / TalkPlayの用途・分割 |
| [DB・取り込み設計](docs/db-design.md) | 保存先・ID・スナップショット・初回取り込みの合意事項 |
| [初回テーブル定義](docs/db-schema.md) | カラム・キー・参照整合性・不変性とmanifest形式 |
| [ローカルDB](docs/local-db.md) | Docker Compose・環境変数・Alembicの手順 |
| [取り込み元確認](docs/music4all-import-sources.md) | Music4Allの実配布ファイル・カラム・取得状況 |
| [Music4All取り込み](docs/music4all-import.md) | Adapter・共通取り込み・100曲/100ユーザー・snapshot読込み |
| [MLflow](docs/mlflow.md) | 共通実験管理の設定・実行 |
| [先行研究](docs/papers/README.md) | 調査候補と記録テンプレート |
| [データ取得](data/README.md) | 出典・照合・加工コマンド |
| [Music4All調査結果](experiments/music4all/summary.md) | 実集計・分割候補・未決定事項 |
