# 開発・運用

## 環境と実装

- macOS / Apple Silicon。Python 3.12をpyenvで選び、uvでプロジェクトの仮想環境・依存を管理する。
- `src/music_recsys/`に実装し、型ヒントを使う。
- 推薦モデル・評価・LLM / Agent・UIを分離する。推薦エンジンを単独で利用できるようにする。
- 必要なディレクトリ・実装だけを段階的に追加し、過度な抽象化を避ける。
- Notebookは分析・可視化を中心にし、データ処理・モデル学習・評価は`src/`のPythonコードを呼ぶ。UIは後で作る。
- `01_data.ipynb`は保存済みデータの読込み・検証・EDA・分割状態の確認、`02_recommendation.ipynb`は推薦の実行・モデルの挙動の理解、`03_analysis.ipynb`は共通指標による性能比較・考察。

```bash
uv sync --locked --python "$(pyenv which python)" --group data --group notebook
```

| 用途 | コマンド |
| --- | --- |
| 合成データの評価 | `uv run --locked music-recsys-benchmark --config configs/toy.toml` |
| MLflowで評価を記録 | `uv run --locked --group tracking music-recsys-benchmark --config configs/toy-mlflow.toml` |
| Notebook | `uv run --locked --group data --group notebook --group tracking jupyter lab` |
| 全テスト | `uv run --locked --group data --group notebook --group tracking pytest` |
| 静的解析 | `uv run --locked ruff check src tests notebooks migrations` |
| 整形確認 | `uv run --locked ruff format --check src tests notebooks migrations` |
| ビルド | `uv build` |

- PostgreSQL + pgvectorはDocker Compose、スキーマ変更はAlembicで管理する。[ローカルDBの手順](docs/local-db.md)
- DB関連のPython依存は`uv sync --locked --group db`で追加する。
- DBテストは明示的に有効化し、専用schemaで実行する。[実行コマンド](docs/local-db.md#dbテスト)
- MLflowは`tracking`依存groupで導入し、専用PostgreSQL DBとGit管理外の成果物保存先を使う。[設定・起動・Notebookの手順](docs/mlflow.md)

## ブランチ

- 基準ブランチは`main`。
- 作業ごとに`main`からブランチを作り、確認後に`main`へ取り込む。
- 初回のリポジトリ構築は`main`に論理単位でコミットする。

| 接頭辞 | 用途 | 例 |
| --- | --- | --- |
| `feat/` | 機能追加 | `feat/item-cf` |
| `fix/` | 不具合修正 | `fix/recall-calculation` |
| `docs/` | 文書変更 | `docs/dataset-policy` |
| `chore/` | 環境・依存・運用 | `chore/update-dependencies` |

## コミット

- 形式は`種別: 日本語の変更内容`。
- 一つのコミットは一つの論理的な変更にする。
- コミット・pushは依頼されたときに行う。

| 種別 | 用途 | 例 |
| --- | --- | --- |
| `feat` | 機能追加 | `feat: 共通Benchmark Runnerを追加` |
| `fix` | 不具合修正 | `fix: 正解なしの評価を修正` |
| `docs` | 文書変更 | `docs: データセットの方針を整理` |
| `chore` | 環境・依存・運用 | `chore: Python環境を構築` |
| `test` | テスト追加・修正 | `test: 時系列分割の検証を追加` |
| `refactor` | 挙動を変えない整理 | `refactor: 評価処理を整理` |

## 文書と管理対象

- 文書は日本語で、ユーザーが共有・決定した内容を必要十分な箇条書き・表で記録する。
- 未承認の提案・仮説・運用ルールを追加しない。未確定事項は未確定と書く。
- 実行結果・再現コマンドは、研究方針と分けて必要な分だけ記録する。
- 論文の未確認の結果・主張を事実として書かず、出典を付ける。
- Notebookの説明は簡潔な箇条書き・表にし、出力をクリアして管理する。

| Gitに含める | Gitに含めない |
| --- | --- |
| コード・テスト・Notebook・依存設定 | 仮想環境・キャッシュ・ビルド生成物 |
| 実験設定・指標・結果の要約 | データ本体・加工済みデータ・学習済みモデル・生ログ |
| データ出典・取得手順 | APIキー・認証情報・`.env`の値 |

- コードのライセンスは未選定。既存の空の`LICENSE`は保持する。
