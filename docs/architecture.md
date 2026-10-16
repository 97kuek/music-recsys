# 現在の構成

## データと評価の流れ

```mermaid
flowchart LR
    Z[Music4All-Onion v2] --> R[raw: 元TSVと配布情報]
    R --> L[lab_data / music4all]
    L --> P[全量events.parquet]
    P --> C[Train観測全ユーザーの履歴]
    C --> T[temporal: 時系列分割]
    T --> E[listening_export: 観測集計]
    E --> B[Validation / Test bundle]
    R --> F[タグ特徴JSON]
    B --> S[full_evaluation / 小規模experiment_search]
    F --> S
    S --> M[recommenders]
    M --> V[Evaluator]
    V --> O[結果JSON / CSV / MLflow SQLite]
    O --> N[Notebookで分析]
```

## モジュール

| 場所 | 責務 |
| --- | --- |
| `full_evaluation.py` / `streaming_metrics.py` | 集約履歴の順次読込み・全件Popularity/ItemCF/ALS/BPR評価 |
| `recommenders/full_factors.py` | ALS/BPRの学習Parquet読込み・候補制約・同点順位の共通処理 |
| `recommenders/full_als.py` / `full_bpr.py` | 各モデルのCPU/GPU学習 |
| `recommenders/gpu_runtime.py` | GPU空き確認・実行環境の記録 |
| `grid.py` | ItemCF近傍・類似度とALS/BPR因子数の順次実行・設定展開・再利用 |
| `comparison.py` | 比較元の出典照合・指標抽出・完了後のレポート公開 |
| `factor_report.py` / `itemcf_report.py` | 因子数比較・ItemCF比較の描画 |
| `full_scoring.py` / `gpu_evaluation.py` / `gpu_metrics.py` | CPU参照評価・GPUバッチ順位・全指標・bootstrap |
| `full_io.py` | 共通の逐次入力・Parquet書込み |
| `full_analysis.py` | 全件の母集団・保存結果の読込み |
| `notebook_config.py` | 設定選択UI・明示的な読込み・変更検知 |
| `configuration.py` | 共通設定の継承・定義元を基準とするパス解決 |
| `lab_data.py` | 取得・照合・ファイル保存のみのコホート生成 |
| `music4all.py` | 全量Parquet変換・全量EDA |
| `temporal.py` | 時刻境界による分割・読戻し検証 |
| `listening_export.py` / `listening_data.py` | v2入力の生成・hashと観測整合性の検証 |
| `features.py` | 重み付きタグ特徴・checksum |
| `recommenders/` | 7モデルの学習・推薦・説明 |
| `candidates.py` / `types.py` | 共通要求・応答・候補の判定 |
| `catalog.py` | 既存IDを維持した外部楽曲参照・文脈別の再生可否・Adapter契約 |
| `model_artifacts.py` / `recommenders/deployed.py` | [全件モデルの配信](model-serving.md)、検証付き保存・学習なしの推論 |
| `profiles.py` | 初期選択と実履歴の区別・曲/artist選択の入力変換 |
| `serving.py` | ModelSpec・Strategy・RecommendationService、登録済み推薦器の選択と推論 |
| `events.py` | 同意済みセッションのメモリ内ログ、[制約と保存設計](online-study.md) |
| `evaluation/` | 精度・多様性・効率の評価、追跡 |
| `experiment_search.py` / `search_protocols.py` | 探索・再利用・選択・確認・固定・Test |
| `search_analysis.py` / `listening_analysis.py` | 保存結果の分析・可視化 |
| `recommendation_inspection.py` | 選択設定の個別推薦・根拠・音源確認 |
| `ingestion/` / `adapters/` / `temporal_cli.py` | 任意のDB経路、[接続手順](optional-storage.md) |

## アプリの共通契約

モジュラーモノリスを維持し、データ取得・学習・推薦戦略・順位付け・再生・ログを分離する
既存のMusic4All item_id、学習済みindex、実験JSONと指標計算は変更しない

| 契約 | 現在の責務・制約 |
| --- | --- |
| TrackRef / CatalogTrack | providerとsource_idを内部item_idへ対応付ける。出典revisionを保持し、同一参照の二重割当てを拒否 |
| TrackLink | 同一録音・バージョンを確認した外部参照だけを追加。曲名・ISRCだけで自動統合しない |
| CatalogAdapter / TrackCatalog | 表示メタデータと文脈別PlaybackAvailabilityを返す。unknown・期限切れは再生成功と扱わない |
| RecommendationRequest / Response | 候補・実履歴・top_kの既存契約。初期選択はprofiles.pyで別入力として明示変換 |
| ModelSpec | モデル版・アルゴリズム・不変のパラメータJSON・seed・学習/カタログ/コードの出典 |
| Strategy | 登録済み推薦器を選択する。現在は固定選択、LLM・Agentによる選択は未実装 |
| RecommendationService | 学習せず推論し、応答ID・重複・top_k・候補制約を検査。選択時間と推論時間を別計測 |
| Playback Adapter | 試聴開始時の音源解決、期限・許諾・失敗の判定。認証トークンを公開メタデータへ保存しない |
| SessionLog | 推薦要求・条件・モデル版と操作イベントを関連付ける。オフライン評価とは別の保存層 |

全件モデルはDeployedRecommenderで共通recommend契約へ接続し、実モデルとmanifestをロード時に照合する
新規楽曲のCatalog登録だけでは既存ALS/BPRの因子を追加しない
新規ユーザー・fold-in・フォールバックは[モデル配信](model-serving.md)を参照
録音の対応表永続化・新規ID採番・Agentの複数ステップ制御は未実装
現在の楽曲データと再生元は[カタログ](catalog-poc.md)、同意と保存境界は[オンラインログ](online-study.md)を参照

## 保存物

| 場所 | 内容 |
| --- | --- |
| `data/raw/music4all-onion-v2/` | 元TSV.bz2・record.json・取得途中の.part |
| `data/processed/music4all-onion-v2/` | 全量Parquet・変換manifest |
| `data/benchmarks/music4all/` | 履歴・分割・v2入力・features.json・ready.json |
| `data/experiments/` | 試行・結果・ログ・tracking.db |
| `configs/popularity-plays.json` | 全件Popularityの設定 |
| `tests/fixtures/configs/workflow-suite-*.toml` | 合成データで7モデルを確認する設定 |
| `tests/fixtures/examples/` | 自動テスト・明示した操作確認用の合成fixture |

## 実験設定

`configs/common.json`にデータパス・評価期間・K・seedを集約し、各実験JSONは`extends`で参照する。
辞書はキー単位で上書き、配列・値は置換する。相対パスはその値を書いたJSONを基準に解決する。
共通設定は実行対象ではない。CLIとNotebookは同じ解決済み設定を使う。
CLIは単一条件が`music_recsys.full_evaluation`、Grid探索が`music_recsys.grid`に統一する。

| 設定 | 出力先（`data/experiments/`以下） |
| --- | --- |
| `configs/popularity-plays.json` / `popularity-users.json` | `popularity-plays/` / `popularity-users/` |
| `configs/itemcf.json` | `itemcf/`と6条件の`itemcf-{similarity}-{neighbors}/` |
| `configs/als.json` | `als/`と`als-{32,64,128}/` |
| `configs/als/256.json` / `512.json` | `als-256/` / `als-512/` |
| `configs/bpr.json` | `bpr/`（他方式との比較も含む）と`bpr-{32,64,128,256}/` |

```mermaid
flowchart LR
    C[common.json] --> E[実験JSONの差分]
    E --> R[CLI / Notebookで同じ設定を解決]
    R --> G[Grid条件を自動展開]
    G --> S[config.jsonに実行条件を固定して保存]
    S --> V[結果と出典を照合して表示]
```

- Gridが生成する`configs/itemcf/`・`configs/bpr/`・ALS 32/64/128は手編集しない
- 生成済み設定は保存結果から参照されるため保持する。Notebookではチェック欄で詳細条件を表示する
- 共通設定変更もNotebookの再読込み対象。保存済み実験と異なる設定で結果を表示しない
- `data/provenance/`は移行前の設定・出典・ログの保管先。通常の実験一覧には含めない

## 保存と再現性

- 元ファイルは配布サイズ・MD5、加工物はSHA-256で照合
- 完了したファイル一式を公開し、途中生成物を完了扱いにしない
- `ready.json` はファイル経路の完了目印、DBのsnapshot公開とは別の仕組み
- ファイル経路のsnapshot / split IDは内容由来の識別子、DB用UUIDとして渡さない
- 実験はデータ・特徴・処理コード・依存lock・設定・seedを記録
- binary-v1のJSON入力とReaderも実装されているが、研究室の標準入力はlistening-v2
- 合成fixtureはテスト・明示した操作確認用で、Notebookから暗黙に読み込まない
- LLM・Agentは未実装、実測使用量を受け取る型のみ存在
