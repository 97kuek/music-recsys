# 保存済み実験結果の分析

## Notebookの構成

| 章 | 内容 |
| --- | --- |
| Overview | 分析目的・評価対象 |
| Setup | データセット・保存結果・評価条件の選択と照合 |
| Search Results | Validationの探索結果・1D線・2Dヒートマップ |
| Model Selection | 主指標による最良・次点、03への引継ぎ、設定固定 |
| Performance Analysis | K別・ユーザー別・CI・効率・トレードオフ。旧保存結果も別条件として維持 |
| Final Comparison | 固定済み設定のTest比較。探索と分離 |
| Summary / Next Actions | 所見・限界・次の操作と戻り先 |

- `02_analysis.ipynb`は保存済み結果の読込み・集計・可視化・統計分析を行う。
- 正式なBenchmark・評価指標の計算は`src/`で実行する。02の既定は読込みのみ。探索・最終TestはCLI、明示した選択・固定は共通APIへ委譲する。
- 共通TOML・状態・保存形式は[検証フロー](experiment-workflow.md)。03は同じ選択artifactを読み、Validationの履歴と推薦を確認する。
- 読込み・条件照合・統計処理は`src/music_recsys/experiment_analysis.py`に置く。
- Music4AllとTalkPlayDataは独立したベンチマークとして選択・分析する。
- 説明は簡潔な箇条書き・表にする。

## 実行・読込み

```bash
uv run --locked music-recsys-benchmark \
  --config configs/toy.toml --no-tracking --output experiments/toy/runs
uv run --locked --group data --group notebook --group tracking jupyter lab
```

| 設定 | 内容 |
| --- | --- |
| `MUSIC_RECSYS_REPORT_PATH` | Popularityの保存済み`results.json`。指定時の既定はMusic4All・K=10 |
| `MUSIC_RECSYS_DATASET_ID` / `MUSIC_RECSYS_TOP_K` | 対象データセットとKを上書き |
| `REPORT_PATHS` | 複数runを比較する場合の表示名 → 保存結果のPath |
| `MLFLOW_RUNS` | 任意：表示名 → MLflow run ID |
| `DETAILS_ROOT` | 任意：CLIの`--output`で指定した親ディレクトリ |
| `BASELINE` | 対応差分の基準run |
| `BOOTSTRAP` | 信頼水準・再標本化回数・乱数seed |
| `HISTORY_EDGES` / `USER_ATTRIBUTE` | 履歴量の区切り・保存済み属性名 |

- 探索未実行時の参考表示は[合成データの保存結果](../examples/reports/toy-popularity.json)をK=2で読む。探索では上位20曲からK=1〜20を共通Evaluatorで計算する。旧K=2の出力をK=20の実行として補完しない。実データへの切替は[Music4Allの実行手順](music4all-benchmark.md)を参照。
- 追加モデル・LLM・Agentも同じ保存形式で読み込む。未実装モデルの値は作らない。
- MLflow集計のみの場合は平均・集計件数を表示し、ユーザー別分析や信頼区間は未分析とする。
- `DETAILS_ROOT`指定時はMLflowの`reports/details.json`から詳細を解決し、SHA-256・run ID・集計との一致を検証する。
- 評価条件が不足する旧形式の結果は読み込み時に通知する。現行CLIで再実行して保存する。

## 比較・統計

- データのSHA-256、分割・学習・入力のSHA-256、候補・正解の定義、K、評価器のSHA-256、集計・失敗の扱いを一致させる。
- 詳細がある場合は要求ID・ユーザー・入力・評価対象数も照合する。対応差分は要求IDで結ぶ。
- 現行の平均は要求を等重みで集計。正解なしは除外、正解がある要求の推薦失敗は0として含める。
- 信頼区間はユーザー単位のpercentile bootstrap・95%。同一ユーザーの全要求をまとめて再標本化し、要求平均の重みを維持する。
- 初期設定は2,000回・seed 42。Notebookで変更可能。対象ユーザー2人未満は信頼区間を未定義とする。
- `user_std`はユーザー平均の標準偏差。`run_std`は同一モデル名・設定・実装コードの実行間の標準偏差。
- モデル設定が未記録の場合は反復runをまとめない。seed一覧・run数・異なるseed数を表示する。
- 履歴量は推薦時点の履歴。属性の欠損は`(missing)`。少数群・少数ユーザーの推定は限界を併記する。
- 失敗分類は重複するため合計しない。未定義・未測定は0にしない。

## 保存形式の追加項目

| 場所 | 項目 | 用途 |
| --- | --- | --- |
| `metadata.evaluation_conditions` | 評価プロトコル・各hash・集計条件 | 比較条件の照合 |
| `metadata.model_parameters` | モデル設定。未記録は`null` | 反復runの識別 |
| `runs[].user_attributes` | 任意の属性辞書 | 属性別分析 |
| `additional_metrics` | 任意の`diversity` / `coverage` / `novelty` | 各指標に`value`と`definition`を保存 |
| `runs[].inference_cost` | 任意の要求別費用 | 全要求分が揃う場合に合計 |
| `metadata.cost_currency` / `cost_basis` | 通貨・算定基準 | 金額の意味を固定 |
| `metadata.measurement_conditions` | 任意の計測条件 | 並列数・キャッシュ等の確認 |

- 任意項目は今後の評価処理が記録するための読込み口。現在のRunnerは属性・追加指標・費用を生成しない。
- Diversityの特徴・類似度、Coverageの対象カタログ、Noveltyの人気度参照期間などは`definition`に含める。異なる定義は比較しない。
- Music4Allの初期評価はNDCG@10・Recall@10と正解のある要求の平均。[100ユーザーの結果](../experiments/music4all/smoke.md)を全量の推薦性能とは扱わない。追加指標の算出方法・研究全体の最終評価条件は未確定。
- 時間・コストは計測環境と併せて解釈する。コストの散布図は通貨・算定基準が一致する場合に表示する。
- 生履歴・要求別の詳細はローカル保存。MLflowには集計と詳細へのmanifestを記録する。
