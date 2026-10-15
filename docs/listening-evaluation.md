# 評価仕様

## 入力と期間

研究室の標準プロトコルは `listening-v2`
再生回数・再生日数を観測事実として保持し、関連度はEvaluatorで計算する

| 項目 | 実装 |
| --- | --- |
| Train | 2017-03-29 00:00:00より前 |
| Validation | Train終端以上、2018-09-21 00:00:00未満 |
| Test | Validation終端以上 |
| 学習 | ValidationではTrain、TestではTrain＋Validation |
| 窓 | 推薦時点から30 / 90 / 180日、開始以上・終了未満 |
| 時刻 | timezone-naiveの配布値、UTC/JSTを割り当てない |
| 母集団 | 学習期間に観測されたユーザーと曲 |
| 既定表示 | 90日・K=10・日数重み1、研究の主タスク・主指標は別途指定 |

Validation窓がTest開始を越える設定は拒否する
配布ログの最終時刻は、各ユーザーを窓末尾まで観測した保証ではない

## 候補と正解

| タスク | 候補 | 指標 |
| --- | --- | --- |
| discovery | 学習カタログ内の本人未聴曲 | Binary NDCG / Recall |
| retention_proxy | discoveryと同じランキング | Graded NDCG |
| repeat | 本人の学習期間の既聴曲 | Graded NDCG / Repeat Recall |

- 正解は評価窓内に1回以上再生した候補内の曲
- 新規とは保存した学習履歴で未聴という意味、生涯初回とは限らない
- retention_proxyは窓内再生の代理指標、推薦の因果効果ではない
- cold user / cold itemを候補へ追加しない
- 正解なしのユーザーも入力・除外件数に残す

## 保存形式

| ファイル | 内容 |
| --- | --- |
| learning.parquet | user_id・item_id・play_count・active_days |
| observations.parquet | user_id・item_id・window_days・学習/評価の回数と日数 |
| manifest.json | protocol・schema版・出典・境界・窓・hash・行数・処理版 |

- 観測行がない組のgainは0、嫌いであることを意味しない
- 新規/既聴は学習回数だけで決める
- hash・列型・一意性・warm条件・回数/日数の整合性を検証
- 推薦モデルへ渡すのは学習履歴・候補・要求、将来の観測値は渡さない
- Loaderは学習2,000ユーザー・500万イベントを超える場合、Python展開前に拒否
- 全量Parquet変換・観測生成と全量モデル評価は別の処理

## 指標

nを評価窓の再生回数、dを異なる再生日数、λを非負の日数重みとする

```text
binary_gain = 1[n >= 1]
graded_gain = log2(1+n) + λ * log2(1+d)
DCG@K = sum(gain(rank_r) / log2(r+1), r=1..K)
NDCG@K = DCG@K / IDCG@K
Recall@K = 先頭K位置で当たったユニーク正解数 / 候補内の全正解数
```

- gainを再度指数変換しない
- IDCGは候補内の全正解から理想上位Kを計算
- Repeat Recallは学習で既聴かつ評価窓で再生された曲を正解とする
- 分母0はnull、正解保有ユーザーを等重みで平均
- 同じタスク・窓・K・λで集計し、評価人数・除外人数も保存
- λは0 / 0.5 / 1 / 2、Kは1〜20を同じランキングから評価

## 出力・失敗

- ユーザーごとにnew/repeat各1回だけ生成、空候補では呼出しを省略
- 先頭20位置を固定し、無効ID・重複IDの位置はgain 0、後続を繰り上げない
- 重複の加点は最初の有効出現のみ、20位超は得点対象外
- 短い出力の不足位置は0、正解ありの推薦失敗も0点
- 正解なしは失敗時もnull、失敗自体は件数に残す
- 不足・重複・無効・超過・失敗をdiagnosticsへ保存

## 多様性・効率

| 指標 | 定義・制限 |
| --- | --- |
| ILD | 共通非負タグのCosine距離、K>=2、全有効曲の非ゼロ特徴が必要 |
| Novelty | 学習ユニークユーザー数による平滑化自己情報量 |
| Catalog Coverage | new/repeat別の候補和集合を分母にした推薦曲割合 |
| Training Time / Latency | 学習時間、生成要求のP50/P95など実測値 |
| Tokens / Cost / Calls / Memory | アダプタが返した実測値のみ、未取得はnull |

- 多様性の集計から正解なしユーザーを除外しない
- 特徴未登録とゼロノルムを区別し、除外理由を保存
- 窓・K・λごとに推薦・コストを重複計上しない
- 使用量の一部欠測を0補完して総量にしない
- CIはユーザー単位bootstrap、ユーザー2人未満は未測定

## 結果と互換性

- JSONはランキング・要求別指標・診断、CSVは条件別集計を保存
- 探索ではresults.json・curves.json・study.jsonなどを保存
- MLflowには条件・集計・成果物参照を記録、生履歴はローカルに保持
- binary-v1は互換入力として残るがv2と同条件で比較しない
- データ・候補・特徴・コード・評価条件が異なる比較を拒否
- 操作は[検証手順](experiment-workflow.md)、モデルは[モデル定義](models.md)
