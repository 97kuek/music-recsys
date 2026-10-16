# music-recsys

音楽推薦システムの研究用実装
従来モデルを共通条件で検証し、LLM・Agentの追加効果を比較するための基盤
LLM・Agent・Web UIは未実装

## 現在の構成

- Python 3.12・uv・srcレイアウト
- Popularity / ItemCF / UserCF / ALS / BPR / Content / Hybrid
- Music4All-Onion v2の再生履歴・タグを使用
- 研究室での標準運用はParquet・JSON・SQLite、Docker・管理者権限不要
- listening-v2で未聴曲の発見・定着の代理指標・既聴曲の再聴を評価
- 共通RunnerがSmoke・Validation探索・選択・確認・固定・Testを管理
- 全学習ユーザーのPopularity / ItemCF / Implicit ALS / BPR Validationに対応、選択用主指標と最終Testは別段階
- ALSのfactors別・既存手法との比較は[モデルと実行方法](docs/models.md#標準parquet経路のimplicit-als)を参照

- BPRの4因子数比較は[モデルと実行方法](docs/models.md#標準parquet経路のbpr)を参照

## 開始

```bash
uv sync --locked --group data --group models --group notebook --group tracking
.venv/bin/python -m music_recsys.lab_data
.venv/bin/python -m music_recsys.full_evaluation --config configs/popularity-plays.json
.venv/bin/jupyter lab --no-browser --ip=127.0.0.1 --port=8888
```

Notebookの選択欄でJSONを選び「読み込む」を押す / 初期状態は未選択
全件Popularityには `popularity-plays.json` を選択
共通設定と各モデルの入口は[実験設定一覧](docs/architecture.md#実験設定)を参照

長時間処理のtmux起動・SSH接続は[研究室の運用](docs/lab-operation.md)を参照
データ準備完了の目印は `data/benchmarks/music4all/ready.json`

| Notebook | 用途 |
| --- | --- |
| [00_lab_start](notebooks/00_lab_start.ipynb) | 準備状態確認・全件Popularityの手動開始 |
| [01_data](notebooks/01_data.ipynb) | 入力・観測分布・分割の確認 |
| [02_analysis](notebooks/02_analysis.ipynb) | 結果分析・設定選択・固定・最終比較 |
| [03_recommendation](notebooks/03_recommendation.ipynb) | 選択設定のValidation推薦を確認 |

## 文書

| 文書 | 内容 |
| --- | --- |
| [考察](docs/discussion.md) | 各方式の観測結果・仮説・LLM/Agentによる戦略選択の研究案 |
| [構成](docs/architecture.md) | モジュール・保存物・データの流れ |
| [運用](docs/lab-operation.md) | 環境・取得・接続・再開・保管 |
| [検証手順](docs/experiment-workflow.md) | Notebook・CLI・状態遷移 |
| [評価仕様](docs/listening-evaluation.md) | 候補・正解・指標・集計・制限 |
| [モデル](docs/models.md) | 学習・スコア・探索パラメータ |
| [任意のDB機能](docs/optional-storage.md) | PostgreSQL・取り込み・分割の接続口 |
| [開発](CONTRIBUTING.md) | 依存・テスト・変更管理 |
| [エージェント指示](AGENTS.md) | 作業ルール・文書とNotebookの書式 |
