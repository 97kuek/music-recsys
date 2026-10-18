# music-recsys

- [English](README.en.md)
- [Macで実際のUIを確認する](docs/mac-preview.md) / 公開URLは未設定（Mac側でTunnel＋Accessの初期設定が必要）
- 従来型推薦・LLM/Agentによる戦略選択を比較する音楽推薦の研究用リポジトリ
- Python 3.12・uv・Parquetによるオフライン評価、React＋TypeScript＋FastAPIの試聴MVP
- LLM/Agentは研究用検証基盤のみ実装、参加者UI・実APIへ未接続。Music4AllのID接続・Web試聴条件は確認が必要

- バックエンドとUIはドメイン・ユースケース・技術依存・表示へ分離、依存方向をテストで検証する

## 始める

```bash
uv sync --locked --group data --group models --group notebook --group tracking
.venv/bin/python -m src.interfaces.cli.lab_data
.venv/bin/python -m src.interfaces.cli.full_evaluation --config configs/popularity-plays.json
.venv/bin/jupyter lab --no-browser --ip=127.0.0.1 --port=8888
```

- 研究室の標準経路は`lab_data → Parquet → CLI評価 → Notebook確認`、Docker・sudo・常駐DBは不要
- Notebookで同じ実験JSONを明示選択して「読み込む」、初期状態は未選択
- 全学習ユーザー・K=2〜20・4観点で比較し、Validationでの比較と最終Testを分ける
- 学習前にホスト`nvidia-smi`を確認、空きGPUを優先、採用モデルや主指標を自動確定しない

## 文書

| 読みたい内容 | 文書 |
| --- | --- |
| 推薦器・共通API・将来のLLM/Agent | [推薦システムのアーキテクチャ](docs/architecture.md) |
| Phase 2〜4の責務分離・既存契約の再利用・不足機能 | [推薦拡張設計](docs/recommendation-design.md) |
| Decisions・LangGraph・LangSmithの任意Adapterと合成検証 | [LLM/Agent検証基盤](docs/llm-agent-evaluation.md) |
| コード構成・Notebook/CLI・保存先・開発 | [コード構成・実行フロー](docs/experiment-workflow.md) |
| 取得元・checksum・提供状況 | [データ出典](docs/data-sources.md) |
| UI/API・データ配置/容量・Mac Docker・Tunnel/Access | [アプリ全体のアーキテクチャ](docs/app-architecture.md) |
| 取得ログ・同意・保存/管理・削除・被験者比較 | [オンライン評価](docs/online-study.md) |
| オンライン指標の式・分母/分子・欠測と集計CLI | [オンライン指標](docs/online-metrics.md) |
| 5人・通常3条件/戦略2条件の割当・研究者分析・CSV/JSON | [オンライン比較実験](docs/online-comparison.md) |
| 試聴・任意評価・将来のReactive Agentの画面と公平性 | [オンラインUI/UX](docs/online-ux.md) |
| 再訪・長期嗜好の同意・確認/修正/削除 | [長期嗜好管理](docs/participant-profile.md) |
| 正解/候補・Recall/NDCG・多様性・効率・欠測 | [オフライン評価指標](docs/listening-evaluation.md) |
| 単独→多段階→LLM選択→Reactive Agentの4フェーズ・未決事項 | [研究計画](docs/research-plan.md) |
| 既存実験の観測・仮説 | [考察](docs/discussion.md) |

## Notebook

| Notebook | 用途 |
| --- | --- |
| [00_lab_start](notebooks/00_lab_start.ipynb) | 入力準備・状態・明示した評価開始 |
| [01_data](notebooks/01_data.ipynb) | 時系列分割・母集団・再生分布 |
| [02_analysis](notebooks/02_analysis.ipynb) | 保存結果の4観点比較・明示選択/固定 |
| [03_recommendation](notebooks/03_recommendation.ipynb) | 選択済み小規模条件の推薦・根拠確認 |

## アプリ・データ保護

- 研究室は学習・オフライン評価、MacのDocker 1コンテナでReact＋FastAPIを配信、Pages/Workersは不要
- Mac版はZIPを展開して`bash deploy/mac.sh start`でローカル確認、遠隔用は`start-remote`＋MacホストのCloudflare Tunnel/Access
- [Macプレビュー手順](docs/mac-preview.md)で、参加者画面と研究者のオンライン分析画面を同じMacから確認できる
- 学習データ/評価結果は研究室、元データ・配信モデル・必要曲の情報/MP3・被験者評価/操作ログはMacへ配置する方針、実転送は未実施
- 参加者ログの保存・保持期間・再開は[オンライン評価](docs/online-study.md#保存権限削除)、容量と転送手順は[アプリ構成](docs/app-architecture.md)
- 研究室の外部公開・ポート開放は禁止、遠隔接続の実地検証はMacで行う
- 生データ・音源・モデル・個別結果・ログ・認証情報はGit管理外、[作業ルール](AGENTS.md)に従う

## アーキテクチャ図

- 現行と将来案を分けたSVG図 / 画像を開くと拡大可能
- ノードの説明は[対話型HTML](docs/architecture.html)、現行/将来のMermaidと仕様は[文書一覧](#文書)を参照。図の実行基盤・融合方式は採用済みではない

### UI・API・DBと配置

![被験者ブラウザからCloudflare AccessとTunnelを経由してMacのReact・FastAPI・推薦サービス・SQLiteへ接続する構成](docs/diagrams/app.svg)

### 研究バッチ・Notebook

![lab_dataからParquetを作りCLIで学習とValidation評価を実行しNotebookで保存結果を読むフロー](docs/diagrams/batch.svg)

### 将来のLLM・Agent

![固定選択・Decisions単発選択・ユーザー明示要求で起動するReactive Agentと未採用の実行基盤候補](docs/diagrams/agent.svg)

### 将来の多段階推薦

![同一候補制約で候補生成・融合・再ランキング・権限検証・提示を行う将来案、方式と構成は未確定](docs/diagrams/ranking.svg)

## ライセンス

- 本リポジトリの原著コード・文書は[MIT License](LICENSE)、適用範囲と第三者資料は[NOTICE](NOTICE)
- データセット・音源・画像・学習済み成果物・参加者ログはMIT対象外、それぞれの提供条件に従う
