# 評価方針

## 共通条件

- 同じデータ・入力条件・評価指標でA / B / Cを実行できる共通Benchmark Runnerを用意する。
- 学習 / validation / test分割、候補楽曲集合、Top-K、指標の定義・計算方法を統一する。
- データリークを防ぎ、実験条件・seed・モデル設定を記録して再現性を確保する。
- 各構成は共通形式の推薦結果を返し、同じEvaluatorで推薦精度を測る。
- LLM / Agent固有の指標を追加しても、推薦精度は共通指標で評価する。
- 構造化嗜好によるモデル比較と、自然言語理解を含む全体比較を分ける。

## 検証基盤

| 要素 | 役割 |
| --- | --- |
| Dataset | ユーザー履歴・楽曲・会話・正解データ |
| Benchmark Runner | データ分割・入力条件・実験設定の統一、A / B / Cの実行 |
| Common Evaluator | NDCG・Recall・Diversity・制約充足率・コストなど |
| Tracking / Reports | MLflowによる実験管理・比較表・統計分析 |
| Notebook | Pythonの処理を呼んで検証・可視化。UIは後から作る |

## 共通形式の検討

| 形式 | 内容 |
| --- | --- |
| RecommendationRequest | 誰にどんな曲を推薦するか。ユーザー情報・再生履歴・会話履歴・要求 |
| RetrievalResult | 推薦候補。楽曲ID・検索スコア |
| RankingResult | 順位付き候補。楽曲ID・順位・推薦スコア |
| RecommendationResponse | 最終結果。おすすめ曲・推薦理由 |
| GroundTruth | 評価用正解。好まれた曲・関連度 |
| EvaluationResult | 評価結果。NDCG・Recall・Diversityなど |

- 正式なJSON Schemaは今後検討する。
- 現在はRequest / Response / GroundTruth / EvaluationResultをPythonの共通型として実装済み。
- RetrievalResult / RankingResult、推薦理由、会話入力は未実装。

## 指標候補

| 分類 | 候補 |
| --- | --- |
| Accuracy | Precision@K、Recall@K、NDCG@K、Hit Rate@K、MAP@K / MRR |
| Beyond-Accuracy | Diversity / Intra-List Diversity、Catalog Coverage、Novelty、Serendipity |
| LLM / Agent | Constraint Satisfaction Rate、Task Success Rate、Explanation Faithfulness、Tool Success Rate、Latency / Cost |
| 主観評価 | ユーザー満足度、推薦理由の納得感 |

- NDCG@10を主指標候補とし、多様性・発見性・実行コストも評価する。
- 初回Music4AllのK・関連度・集計条件は下表。研究全体の最終評価条件は未確定。
- 現在の合成データではNDCG / Recallを実装済み。条件と結果は [toy実験](../experiments/toy/summary.md) を参照。

## 保存済み結果の分析

- 03は同条件の保存結果を比較し、性能・ユーザー間の差・効率を分析する。
- 信頼区間はユーザー単位bootstrap・95%。実行間のばらつきとは分ける。
- [6章構成・比較条件・保存形式](result-analysis.md)

## Music4Allの初回実データ検証

| 項目 | 条件 |
| --- | --- |
| Train | `event_time < 2017-03-29T00:00:00` |
| Validation | `2017-03-29T00:00:00 <= event_time < 2018-09-21T00:00:00` |
| Test | `2018-09-21T00:00:00 <= event_time` |
| タイムゾーン | 配布時刻のタイムゾーンは未確認。timezone-naive値のまま比較し、UTC・JSTへの変換はしない |
| 学習 | ValidationはTrain、TestはTrain＋Validationで再学習 |
| 候補 | 学習期間で観測された楽曲から、そのユーザーの学習期間の全既聴曲を除外 |
| GroundTruth | 評価期間に1回以上再生した、候補内のユニーク楽曲。関連度は二値 |
| Warm Start | 学習期間で観測されたユーザー・楽曲のみ。TestではValidationの観測も含む |
| 指標 | NDCG@10・Recall@10。正解のある要求を平均し、正解なしの件数を別記 |
| 調整 | Validationまで。Testは条件固定後の最終評価 |
| 初回対象 | Trainに履歴のあるユーザーを元ID順に100人選び、その全期間の履歴を使用。楽曲数の上限なし |
| 規模・seed確認 | Validationで人数・ユーザー抽出seedを変更。固定データでモデルseed・反復実行も比較 |

- 除外は`cold_user → cold_item → seen → eligible`の排他的な順で数える。イベント数・ユニークuser-trackペア数・ユーザー数・楽曲数を保存する。
- 各分割の件数・期間、学習母集団、評価可能ユーザー、正解曲数を記録する。
- 期間の重なり・取りこぼし、Parquet読戻し、Warm Start、既聴曲と正解の交差を検証する。未来のイベントを変えても過去の学習・候補が変わらないことをテストする。
- 楽曲タグはカタログに保持するが、今回のPopularityの学習・順位付けには使用しない。
- 小規模な動作確認結果と全量での推薦性能を区別する。Music4AllとTalkPlayDataは別ベンチマークとする。
