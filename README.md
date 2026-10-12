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
- PostgreSQL + pgvectorのローカルDocker環境とAlembicを構築済み。
- LLM・Agent・UIは今後実装。UIより先に検証基盤を整える。

## 文書

| 文書 | 内容 |
| --- | --- |
| [CONTRIBUTING](CONTRIBUTING.md) | 環境・実行・ブランチ・コミットのルール |
| [研究計画](docs/research-plan.md) | 背景・課題・比較・進め方 |
| [評価方針](docs/evaluation-protocol.md) | 共通基盤・形式・指標候補 |
| [データセット方針](docs/dataset-investigation.md) | Music4All / TalkPlayの用途・分割 |
| [ローカルDB](docs/local-db.md) | Docker Compose・環境変数・Alembicの手順 |
| [先行研究](docs/papers/README.md) | 調査候補と記録テンプレート |
| [データ取得](data/README.md) | 出典・照合・加工コマンド |
| [Music4All調査結果](experiments/music4all/summary.md) | 実集計・分割候補・未決定事項 |
