# 推薦システムのアーキテクチャ

## 責務と境界

- 対象は推薦の学習・候補制約・戦略選択・順位付け・評価
- UI・音源配信・配置は[アプリ全体](app-architecture.md)、コードと保存先は[実行フロー](experiment-workflow.md)
- アイコン付き全体図・DDD/Cleanの将来案は[設計図HTMLのソース](https://github.com/97kuek/music-recsys/blob/main/docs/architecture.html)をブラウザで開く
- モジュラーモノリスを維持し、推薦器と推薦戦略を分離する
- Music4Allの既存item_id・実験結果・学習index・Pythonパッケージ名を維持する

```mermaid
flowchart LR
    T[学習期間の履歴] --> B[バッチ学習]
    F[学習カタログの特徴] --> B
    B --> M[学習済み推薦器 / ModelSpec]
    U[要求・初期選択・実履歴] --> C[候補制約 new / repeat]
    C --> S[Strategy: 登録済みモデルを選択]
    M --> S
    S --> R[RecommendationService]
    R --> O[順位付き曲ID・経路・使用量]
    O --> E[Evaluator]
    V[Validation / Testの観測] --> E
    O --> L[オンライン研究ログ]
```

- 推薦器へ渡すのは学習由来の状態・要求・候補のみ
- 将来の正解はEvaluatorだけが読み、候補選択や負例抽出へ渡さない
- newは学習カタログの本人未聴曲、repeatは本人既聴曲に制限する
- 初期選択と実際の再生履歴は別入力とし、初期選択をRepeat Recallの正解にしない

## 推薦器

| 方式 | 学習・順位付け | 実装範囲 |
| --- | --- | --- |
| Popularity | 学習総再生回数またはユニーク再生ユーザー数 | 小規模・全件評価・配信 |
| ItemCF | 二値曲共聴のCosine/Jaccard、履歴曲の上位近傍スコア和 | 小規模・全件評価・配信 |
| BM25 ItemCF | 再生回数をBM25重みへ変換した曲内積、履歴近傍スコア和 | 全件評価・ItemCF形式の配信 |
| UserCF | 本人を除く類似ユーザーの既聴曲へ近傍重みを加算 | 小規模共通Runner |
| Implicit ALS | 信頼度付き行列分解、ユーザー・楽曲因子の内積 | 小規模・全件CPU/GPU・配信 |
| BPR-MF | 観測正例を未観測負例より高くするペア学習、因子内積 | 小規模・全件CPU/GPU・配信 |
| Content | 学習カタログのタグTF-IDFと履歴プロファイルのCosine | 小規模・全件GPU評価・Jamendoタグ配信 |
| Hybrid | ItemCF/UserCFとContentの順位を重み付きRRFで融合 | 小規模共通Runner |

- Contentの曲類似は聴取履歴由来のItemCFと区別する
- MTG-Jamendo/FMAの曲特徴だけではユーザー×楽曲履歴を作れない
- 楽曲統計・タグ頻度を、ユーザー履歴のPopularity/協調フィルタと読み替えない
- 同点は原則として学習再生回数降順・item_id昇順、Popularityはスコア降順・item_id昇順
- 根拠不足のPopularity補完と実際の推論経路を記録し、方式固有の成果として数えない

## モデルごとの重要な条件

| 方式 | 現行の全件設定・制約 |
| --- | --- |
| ItemCF | cosine/jaccard × neighbors=20/50/100、履歴を二値化、自己近傍を除外 |
| BM25 | K1=1.2、B=0.75、neighbors=20、曲共起を128曲ブロック処理、推論履歴は二値 |
| ALS | factors=32/64/128/256/512、iterations=20、alpha=10、regularization=0.1、seed=0 |
| ALS信頼度 | 既定は `c_ui=1+alpha*log1p(n_ui)`、`p_ui=1[n_ui>0]`、未観測の信頼度1を含む |
| ALS数値解法 | CPUはfloat64直接解法、512のGPUはfloat32共役勾配・cg_steps=20、差を因子数だけの効果にしない |
| BPR | implicit 0.7.2、factors=32/64/128/256、iterations=30、lr=.01、reg=.01、seed=0、float32 |
| BPR負例 | 学習CSRの楽曲エントリ由来、既聴負例を棄却して更新スキップ、全曲既聴ユーザーはPopularity補完 |
| Content | 学習曲だけでIDFを計算、L2正規化した曲特徴をbinary/log_count履歴で集約、欠損を嗜好根拠にしない |
| Hybrid | CFとContentのRRF、cf_weight・rrf_constant・pool_sizeを明示、未取得成分の寄与は0 |

- 小規模BPRは未聴曲の一様抽出、全件implicit BPRとは負例分布・bias・更新方式が異なる
- CPU/GPUのsolver・精度・並列更新順の違いを出典へ残し、seed固定でもビット一致を保証しない
- 長時間計算前にホスト側の`nvidia-smi`を確認し、空いていれば対応GPU実装を優先する
- 学習だけでなく順位付け・指標集計・bootstrapもGPU化の対象とする
- 全件Contentの疎行列モデルexportは未実装、既存Jamendo Content配信とは別経路

## 共通契約

| 契約 / 実装 | 責務 |
| --- | --- |
| RecommendationRequest / Response | request_id・user_id・履歴・候補・task・top_k / 順位付き曲ID・状態・使用量 |
| InitialProfile / ProfileRequest | 曲・内部artist IDの初期選択を要求へ変換、実履歴とinput_kindで区別 |
| ModelSpec | モデル版・アルゴリズム・不変のパラメータ・seed・学習/カタログ/実装の出典 |
| Strategy / FixedStrategy | 登録済みModelSpecから利用モデルを選択、現在は固定選択 |
| RecommendationService | 学習せず推薦、応答ID・重複・top_k・候補を検査、選択時間と推論時間を別計測 |
| DeployedRecommender | 全件モデル配列を共通recommend契約へ接続、fold-in/補完経路を明示 |
| Evaluator | 同じ候補・正解・期間で4観点を採点、[オフライン評価](listening-evaluation.md)に従う |

## 保存済みモデルとCold-start

- `model_artifacts.export_model/load_model`を使用し、manifest＋NPYで保存、pickleは使わない
- manifestにModelSpec・学習hash・Git revision・ソース/lock hash・配列hash/shape/dtype・実行環境を保持
- item順序hash、ファイル集合、有限値、index範囲・重複、manifest hashをロード時に検証する
- NPYをread-only mmapで開き、推論要求ごとに重みを読み直さない
- 既存保存先へのexportを拒否、モデル更新は新しい版とディレクトリを使う
- 過去の評価JSONからALS/BPRの重みを復元することはできない

| 入力 | Popularity | ItemCF/BM25 | ALS | BPR |
| --- | --- | --- | --- | --- |
| 既存ユーザー | 保存統計 | 入力履歴の近傍 | 保存ユーザー因子 | 保存ユーザー因子 |
| 新規ユーザーの初期選択 | 保存統計 | 学習曲の近傍 | 明示有効化したfold-in | 因子適応は未実装、Popularity |
| 学習外の新曲 | 学習統計なし | 近傍なし | 因子なし | 因子なし |

- ALS fold-inは固定楽曲因子と学習時のalpha/正則化で小さな正規方程式を解く、全モデルの再学習ではない
- 初期選択を二値重み1で扱うfold-inの推薦品質は別途検証が必要
- BPR未知ユーザーのPopularity補完をBPRとして比較しない、必要経路の不一致を実験計画で拒否する
- Catalog登録だけではCFの新曲推薦は増えない、特徴ベース経路またはバッチ再学習が必要

## 将来のオーケストレーション

```mermaid
flowchart LR
    Q[同じ要求・候補・情報権限] --> F[固定 / ルール選択]
    Q --> L[LLM / OpenAI Decisions API: 1回選択]
    Q --> A[Agent: 選択・実行・結果確認・再判断]
    F --> S[既存Service / 登録済み推薦器]
    L --> S
    A --> S
    S --> A
    S --> E[同じEvaluator / オンラインログ]
```

- 現在の実装は固定Strategyまで、ルール選択・LLM・軽量意思決定API・Agentは未実装
- [OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions)はchoice/predicate/scoreを返す判断用の候補、登録推薦器の選択や条件判定に使う構想
- 文章生成やtool呼出しは[Responses API](https://developers.openai.com/api/docs/guides/function-calling)、複数段の進行制御はアプリ側オーケストレーターで扱う
- 単発選択は「どの推薦器/設定を使うか」、Agentは「結果を見て次に何をするか」を比較対象にする
- 選択候補は検証済み登録モデルに限定、LLMにモデルファイル・DB・音源への任意アクセスを与えない
- 候補・初期プロフィール・K・呼出し上限・時間/料金予算を揃え、再推薦回数・tool callも測る
- Agentの複数回推薦と固定方式の1回推薦は、回数予算を含む別条件として明示する
- 主指標と採用設定を自動確定せず、[研究計画](research-plan.md)に沿って段階的に検証する

## 参考

- [Implicit ALS原論文](https://yifanhu.net/PUB/cf.pdf)
- [BPR原論文](https://proceedings.mlr.press/r7/rendle09a.html)
- [implicit近傍・BM25仕様](https://benfred.github.io/implicit/api/models/cpu/knn.html)
- [RRF原論文](https://research.google/pubs/reciprocal-rank-fusion-outperforms-condorcet-and-individual-rank-learning-methods/)
