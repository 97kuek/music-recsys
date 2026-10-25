# 推薦の評価結果と考察

## 比較仕様と原本

- Train＋Validationの227,677,541再生・119,133人・53,336曲で再学習し、2018-09-21以降のTestを評価する
- 以下の主表示は90日・K20・日数重み1。新規精度14,069人、再聴精度14,594人の等重み平均。正解なし105,064人／104,539人を精度平均から除外する
- 推薦と多様性の母集団は全119,133人。K2〜20・30/90/180日・全日数重み・4観点・再生回数/日数分布を保存し、[評価仕様](evaluation.md)を維持する
- 比較は23パラメータ設定・37学習（BPRのみ3seed）。表示の優先順は選択用の主タスク/主指標を確定せず、全条件を採点前に固定してTestによる採用・追加調整は行わない
- [凍結ソース](../data/provenance/remaining-test-20261025T055046Z/snapshot.json) / [実装の確認](../data/provenance/remaining-test-20261025T055046Z/verification.json)。探索値は[Validation診断](discussion.md#validationの結果)へ分離する

| 方式 | 事前固定した比較 | Test原本 |
| --- | --- | --- |
| EASE | 正則化λ100/500/2000、3設定 | [CSV](../data/experiments/ease/test/metrics.csv) / [JSON](../data/experiments/ease/test/result.json) / [対応差](../data/experiments/ease/test/paired.csv) |
| Content | IDFあり/なし×binary/log_count、4設定 | [CSV](../data/experiments/content/test/metrics.csv) / [JSON](../data/experiments/content/test/result.json) / [対応差](../data/experiments/content/test/paired.csv) |
| BM25 | k1=1.2/b0.75の5近傍数＋Validationの4精度指標の最大条件、9設定 | [CSV](../data/experiments/bm25/test/metrics.csv) / [JSON](../data/experiments/bm25/test/result.json) / [対応差](../data/experiments/bm25/test/paired.csv) |
| BPR | 500epochで4因子数、128因子で4epoch、重複を除く7設定×3seed、21学習 | [CSV](../data/experiments/bpr/test/metrics.csv) / [JSON](../data/experiments/bpr/test/result.json) / [対応差](../data/experiments/bpr/test/paired.csv) |

- BPRの因子数は32/64/128/256、epochは30/100/200/500、seed42/43/44。lr0.01/reg0.01を固定する
- BM25の基準近傍数は10/20/50/100/200。追加4条件はn20/k0.3/b0.75、n10/k4.8/b0.75、n100・200/k4.8/b0.25。Test前のValidationに基づく条件で、Test探索とはしない
- 同じTestの既存13条件を参照する。[Popularity](discussion.md#単独方式の比較) / [ItemCF](discussion.md#単独方式の比較) / [ALS](discussion.md#単独方式の比較)の数値・原本は保持する

## EASE: 正則化による精度と推薦の広がり

| λ | 新規Binary NDCG | 新規Recall (%) | 既聴Graded NDCG | Repeat Recall (%) |
| --- | ---: | ---: | ---: | ---: |
| 100 | 0.032717 | 2.2963 | 0.104388 | 5.4668 |
| 500 | 0.034292 | 2.3103 | 0.111056 | 5.6310 |
| 2000 | 0.035912 | 2.3628 | 0.115485 | 5.6881 |

| λ・新規K20 | ILD / 有効人数 | Coverage (%) | Novelty (bit) |
| --- | --- | ---: | ---: |
| 100 | 0.735111 / 111,960 | 70.7046 | 5.5848 |
| 500 | 0.715310 / 114,005 | 66.2836 | 5.4670 |
| 2000 | 0.684380 / 115,682 | 60.8013 | 5.2679 |

- 観測: この3条件ではλ2000の4精度平均が最大。2000−500の新規NDCG差は+0.001620、対応あり95%区間[+0.001136,+0.002113]、既聴NDCG差は+0.004429[+0.003663,+0.005189]
- 観測: 同じ比較の新規Recall差は+0.0525ポイント[−0.0051,+0.1087]、Repeat Recall差は+0.0571ポイント[−0.0250,+0.1382]で0を含む。Recallまで明確に改善したと一括して述べない
- 観測: 正則化を強めるほどCoverageとNoveltyは低下する。ILDの原本では有効人数が異なるため、後述の共通ユーザーの値と分ける
- 既存比較: λ500−ALS256の新規NDCG差は+0.004420[+0.003127,+0.005680]、新規Recall差は+0.5722ポイント[+0.4297,+0.7177]。Repeat Recall差の区間は0を含み、全タスクで優位とはしない
- 解釈: 上位順位の精度と露出の広がりにトレードオフがある。共起の細かい差を強い正則化が抑えた可能性はあるが、改善曲・人気の寄与は未分離
- 限界: λ2000は今回の探索端であり、全域の最適値や汎用的な推奨値を確定しない
- 効率: 学習113.3秒前後、評価135.6〜136.9秒、順位取得はGPU128人バッチの償却値0.237〜0.238ms/要求。小さな時間差から速度の優劣を結論づけない
- 資源: FP32 Cholesky、正確な密な曲間係数を保持。GPUの計画ピーク約22.96GiBと学習後占有約11.26GiBは異なる量で、単発API応答時間や実測最大VRAMへ読み替えない
- 本文候補: [新規NDCG](../data/experiments/ease/test/binary_ndcg_k.png) / [既聴NDCG](../data/experiments/ease/test/graded_repeat_k.png) / [Coverage](../data/experiments/ease/test/catalog_coverage_new_points.png)。補足: [新規Recall](../data/experiments/ease/test/recall_k.png) / [Repeat Recall](../data/experiments/ease/test/repeat_recall_k.png)

## Content: タグの希少性と履歴の重み付け

- 学習カタログ内のタグIDFと正規化タグベクトルを使い、履歴をbinaryまたはlog(1＋再生回数)で集約したプロファイルとのcosineで推薦する

| IDF | 履歴集約 | 新規Binary NDCG | 新規Recall (%) | 既聴Graded NDCG | Repeat Recall (%) |
| --- | --- | ---: | ---: | ---: | ---: |
| あり | binary | 0.008620 | 0.5066 | 0.060490 | 3.8033 |
| あり | log_count | 0.009385 | 0.5727 | 0.070573 | 4.3321 |
| なし | binary | 0.006473 | 0.3672 | 0.054972 | 3.5156 |
| なし | log_count | 0.007085 | 0.3971 | 0.061394 | 3.8612 |

| IDF・履歴集約・新規K20 | ILD / 有効人数 | Coverage (%) | Novelty (bit) |
| --- | --- | ---: | ---: |
| あり・binary | 0.245818 / 119,133 | 42.6354 | 6.7432 |
| あり・log_count | 0.246044 / 119,133 | 44.2009 | 6.7784 |
| なし・binary | 0.224728 / 119,133 | 43.4753 | 6.3628 |
| なし・log_count | 0.225414 / 119,133 | 44.7784 | 6.3878 |

- 観測: IDFありでlog_count−binaryの新規Recall差は+0.0661ポイント[+0.0260,+0.1091]、Repeat Recall差は+0.5288[+0.4463,+0.6111]。既聴NDCG差も+0.010083[+0.009288,+0.010854]
- 観測: log_count固定でIDFあり−なしの新規Recall差は+0.1757ポイント[+0.1216,+0.2312]、Repeat Recall差は+0.4709[+0.3506,+0.5814]。一方、IDFなしのlog_count−binaryでは新規Recall差の区間が0を含む
- 観測: この4条件ではIDFあり/log_countの4精度平均が最大。IDFを付けると新規NoveltyとILDは高まるがCoverageは低く、全ての多様性指標が改善するわけではない
- 既存比較: IDFあり/log_countは再生回数Popularityより新規Recallが+0.1086ポイント[+0.0321,+0.1841]だが、新規NDCGは−0.001324[−0.002164,−0.000468]。人数Popularityとの新規Recall差は0を含み、単に個人化すれば全指標が上がるとはいえない
- 解釈: 頻繁に聴く曲を強調する集約と希少タグの重み付けが、タグ嗜好の識別に寄与した可能性。ただし2要因の交互作用の検定や改善ユーザーの特徴分析は行っていない
- 限界: 再聴では履歴曲自身もプロファイルへ寄与する。既聴の適合と未聴曲の発見を分け、タグ空間での集中・分散を音の多様性や満足度へ一般化しない
- 効率: CPUで疎な特徴を準備する学習61.2〜62.0秒、GPU評価143.6〜146.5秒、順位取得の償却値約0.305ms/要求
- 本文候補: [新規Recall](../data/experiments/content/test/recall_points.png) / [Repeat Recall](../data/experiments/content/test/repeat_recall_points.png) / [Novelty](../data/experiments/content/test/novelty_new_points.png)。補足: [K曲線](../data/experiments/content/test/recall_k.png)

## BM25: 近傍数と重み付けの効果を分ける

- 曲×ユーザーの再生回数行列へBM25重みを付け、曲間内積の上位n近傍を使う。推論ではbinary履歴を使い、Contentのタグは使わない。表の上5行はk1/b固定、下4行はValidation由来の比較条件

| n / k1 / b | 新規Binary NDCG | 新規Recall (%) | 既聴Graded NDCG | Repeat Recall (%) |
| --- | ---: | ---: | ---: | ---: |
| 10 / 1.2 / 0.75 | 0.023851 | 1.5371 | 0.074730 | 4.0756 |
| 20 / 1.2 / 0.75 | 0.025275 | 1.5191 | 0.083549 | 4.4139 |
| 50 / 1.2 / 0.75 | 0.026288 | 1.4891 | 0.090726 | 4.6605 |
| 100 / 1.2 / 0.75 | 0.026005 | 1.3999 | 0.091942 | 4.8129 |
| 200 / 1.2 / 0.75 | 0.025643 | 1.3501 | 0.091750 | 4.8466 |
| 10 / 4.8 / 0.75 | 0.021816 | 1.4649 | 0.064770 | 3.6875 |
| 20 / 0.3 / 0.75 | 0.027038 | 1.5461 | 0.090973 | 4.7458 |
| 100 / 4.8 / 0.25 | 0.025954 | 1.3652 | 0.092729 | 4.9443 |
| 200 / 4.8 / 0.25 | 0.025645 | 1.3081 | 0.091864 | 4.9293 |

- 観測: 基準設定のn200−10は新規Recall−0.1870ポイント[−0.3067,−0.0694]、Repeat Recall+0.7710[+0.6309,+0.9132]。新規NDCGの平均はn50、既聴NDCGはn100で最大となり、Recallだけで順位品質を代用しない
- 観測: 同じn20/b0.75でk1を1.2→0.3へ下げると、新規NDCG+0.001763[+0.001004,+0.002505]、Repeat Recall+0.3319ポイント[+0.2335,+0.4306]。新規Recall差の区間は0を含む
- 観測: このk1比較では新規Coverageが38.9831%→31.2472%、Noveltyが4.8436→4.1927bitへ低下。精度の向上と露出範囲の縮小が併存する
- 観測: 基準n10→200でも新規Coverageは40.8542%→35.4564%、Noveltyは4.9296→4.2381bit。ILDは0.664880/117,869人→0.496516/118,277人で、人数差を除いた比較は後述の共通ユーザーの値で行う
- 解釈: 広い近傍で頻出する共起構造を拾うことが再聴に寄与し、未聴曲の発見や露出を狭めた可能性。重み付けによる人気・再生回数の寄与は未分離で、因果説明とはしない
- 限界: Validationで最大だった条件がTestでも各指標最大になるとは限らない。例えばn10/k4.8/b0.75は同じn/bの基準より平均が低い。時期・母集団が異なるため、これだけで過学習を断定しない
- 限界: n100/k4.8/b0.25の再聴平均はn200の同設定より高いが、Repeat Recall差の区間は0を含む。複数パラメータを変えた差をn単独の効果やTestでの最適値へ読み替えない
- 効率: 学習104.0〜104.3秒、評価93.3〜102.4秒、順位取得のGPU償却値0.116〜0.141ms/要求。各条件を独立学習し、近傍表の再利用による短縮は含めない
- 本文候補: [近傍数と新規Recall](../data/experiments/bm25/test/recall_neighbors_points.png) / [Repeat Recall](../data/experiments/bm25/test/repeat_recall_neighbors_points.png) / [新規Coverage](../data/experiments/bm25/test/catalog_coverage_new_neighbors_points.png)。別設定のTest: [新規Recall](../data/experiments/bm25/test/recall_validation_points.png)

## BPR: 学習回数と因子数を分けて比較する

- 二値履歴・項目bias付きのGPU FP32 SGD。正例・負例はCSR要素から抽出し、観測済みの負例はskipする。カタログ一様負例とは異なり、GPUは同seedでもbitwise一致を保証しない
- 表は3seed平均。128因子でepochを変える系列と、500epochで因子数を変える系列であり、全因子数×全epochの実験ではない

| 因子数 / epoch | 新規Binary NDCG | 新規Recall (%) | 既聴Graded NDCG | Repeat Recall (%) | 学習秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 128 / 30 | 0.023221 | 1.2017 | 0.095752 | 5.4450 | 71.8 |
| 128 / 100 | 0.026781 | 1.4401 | 0.103081 | 5.7968 | 96.9 |
| 128 / 200 | 0.028302 | 1.5190 | 0.104976 | 5.8862 | 133.1 |
| 32 / 500 | 0.023337 | 1.2755 | 0.097525 | 5.6312 | 152.5 |
| 64 / 500 | 0.026063 | 1.3991 | 0.102092 | 5.7918 | 167.8 |
| 128 / 500 | 0.028447 | 1.5158 | 0.106019 | 5.9054 | 241.0 |
| 256 / 500 | 0.030084 | 1.5856 | 0.108220 | 5.9985 | 396.2 |

- 観測: 128因子の500−30epochでは、新規Recallが+0.3141ポイント[+0.2454,+0.3804]、Repeat Recallが+0.4604[+0.3413,+0.5641]。100→200でも4精度の対応差の区間は0を含まない
- 観測: 500−200epochの新規Recall差は−0.0032ポイント[−0.0427,+0.0386]、Repeat Recall差は+0.0192[−0.0398,+0.0777]。新規NDCG差も0を含むが、既聴NDCG差は+0.001043[+0.000507,+0.001553]
- 解釈: 後半のRecallの伸びは小さい一方、既聴の順位品質には改善が残る。学習秒は200→500で約1.81倍となり、全指標の収束や200epochの採用をこのTestから確定しない
- 観測: 500epochでは因子数とともに4精度の平均が上がる。256−32因子の新規Recall差は+0.3101ポイント[+0.2395,+0.3788]、Repeat Recall差は+0.3673[+0.2709,+0.4633]。256は探索端で、最適因子数や上限とはしない
- 観測: 32→256因子で新規Noveltyは7.0666→6.8177bit、Coverageは94.5140%→94.0872%。一方、128因子の30→500epochではNovelty6.4706→6.8457bit、Coverage80.5485%→94.2959%で、学習回数と表現容量の影響を同一視しない
- 既存比較: 128因子/500epoch−ALS256の新規Recall差は−0.2223ポイント[−0.3304,−0.1132]、Repeat Recall差は+0.3396[+0.1828,+0.5001]。新規発見と再聴で方向が異なり、因子数・反復数・学習目的の差を含むためモデル構造だけの効果とはしない
- 効率: 学習71.8〜396.2秒、評価の3seed平均100.7〜101.5秒、GPU128人バッチの順位取得償却値約0.108〜0.110ms/要求。小さな時間差を単発API速度の優劣へ読み替えない
- 本文候補: [epochと新規Recall](../data/experiments/bpr/test/recall_epochs_points.png) / [因子数とRepeat Recall](../data/experiments/bpr/test/repeat_recall_factors_points.png) / [因子数とNovelty](../data/experiments/bpr/test/novelty_new_factors_points.png)。補足: [全Kのepoch比較](../data/experiments/bpr/test/recall_epochs_k.png)。BPR内比較の誤差棒はseed SD

## 共通ユーザーでのILD確認

- task/Kごとに、各方式内の全条件・全seedで特徴が有効なユーザーの共通集合を使う。方式間では集合が異なり、この表から方式のILDを直接順位付けしない
- BPRの共通集合は21学習全体で求め、条件ごとの3seed平均とseed SDを算出する。元の有効人数・除外人数・平均値も別に保持する
- [個別精度・原本hash・図の確認結果](../data/experiments/comparison/remaining-test/result.json) / [元ILDとの照合](../data/experiments/comparison/remaining-test/metrics.csv) / [共通集合のILD](../data/experiments/comparison/remaining-test/ild_common.csv)

| 方式 | 新規共通人数 | 新規ILDの条件平均の範囲 | 再聴共通人数 | 再聴ILDの条件平均の範囲 |
| --- | ---: | --- | ---: | --- |
| EASE | 111,122 | 0.682758〜0.734694 | 114,262 | 0.594354〜0.652000 |
| Content | 119,133 | 0.224728〜0.246044 | 117,633 | 0.372414〜0.382779 |
| BM25 | 115,450 | 0.495469〜0.674362 | 114,831 | 0.508079〜0.611482 |
| BPR | 66,496 | 0.541897〜0.564883 | 96,255 | 0.561559〜0.575622 |

- 表はK20、BPRは同じ共通集合での3seed平均。EASEのλ100→2000で新規ILDは0.734694→0.682758、BM25基準のn10→200では0.664039→0.495469で、人数差を除いても低下する
- BPRの128因子/30→500epochでは新規ILD0.564883→0.541897。新規の共通集合は全体の約55.8%と狭く、全学習ユーザーへの多様性の一般化とは区別する。[BPRの共通ILD図](../data/experiments/comparison/remaining-test/ild_bpr_new.png) / [EASEの共通ILD図](../data/experiments/comparison/remaining-test/ild_ease_new.png)
- 37学習の全精度23,199行を個別Parquetから再計算し、凍結411ファイルと元の図180組を照合した。全1,406行のILD有効人数は一致し、1e-10を超える元平均との差は4行、最大約1.34e-8
- 数値上の制約: BPR32因子/500epoch/seed44の新規K17では、元の疎な曲ペアGPU経路と独立FP64基準の間に1人で0.001203、全体平均で約1.34e-8の差を検出した。元経路では保存値を再現し、累積GPU法はCPU FP64と約2.5e-16で一致する。[切り分け記録](../data/provenance/remaining-test-20261025T055046Z/ild-diagnostic-batch.json)を保持し、原本の訂正や単なる丸め誤差とは扱わない
- 共通ILDは検証した累積法の値とし、元ILDとの人数一致を必須にする。平均差は絶対1e-7で検査し、1e-10を超える差を確認結果へ全件保存する。推薦順位・個別精度・元の図と数値は変更しない

## 推定と解釈の共通条件

- 対応bootstrap・分母・特徴の時点可用性・タグ依存・時間比較・欠測は[共通評価仕様](evaluation.md#不確実性と選択)に従う。差は右−左、90日/K20/重み1の95%区間は多重比較補正なしの探索的な区間
- BPRのK/パラメータ図は平均±seed SD、方式比較のK20点は固定3seedのユーザー平均に条件付けた区間。未聴Graded NDCGの3seed平均のユーザー区間は未測定
- 既存方式のTestは既に確認されている。今回の37学習は事前固定した追加比較であり、完全未見の一回限りの外部検証とは区別する

## 単独方式の比較

- 以下は同じTestの比較条件の例で、採用版や主指標の宣言ではない。全条件・4観点・K感度は原本CSVと図を参照する

| 方式・条件 | 新規Binary NDCG | 新規Recall (%) | 既聴Graded NDCG | Repeat Recall (%) |
| --- | ---: | ---: | ---: | ---: |
| Popularity 再生回数 | 0.010709 | 0.4641 | 0.067693 | 4.2683 |
| Popularity 人数 | 0.012418 | 0.5324 | 0.066780 | 4.1216 |
| ItemCF Cosine20 | 0.027440 | 1.8248 | 0.081857 | 4.3670 |
| ItemCF Jaccard20 | 0.025505 | 1.7159 | 0.067093 | 3.7223 |
| ALS 32因子 | 0.025666 | 1.3112 | 0.095789 | 5.3989 |
| ALS 64因子 | 0.026499 | 1.4102 | 0.097890 | 5.5278 |
| ALS 128因子 | 0.028176 | 1.5595 | 0.097851 | 5.6481 |
| ALS 256因子 | 0.029872 | 1.7381 | 0.093430 | 5.5658 |
| ALS 512因子 | 0.029982 | 1.8908 | 0.080631 | 5.1970 |

- 原本: [Popularity](../data/experiments/popularity/test/result.json) / [ItemCF](../data/experiments/itemcf/test/result.json) / [ALS](../data/experiments/als/test/result.json)。[ALS新規Recall](../data/experiments/als/paper/recall_test.png) / [Repeat Recall](../data/experiments/als/paper/repeat_recall_test.png)
- ALSの新規Recall平均は因子数とともに上昇する一方、Repeat Recallは128因子で最大。256−128のRepeat差の区間は0を含み、512−128では低下する。最適因子数や頭打ちを一律に確定しない

## Validationの結果

- Testと時期・カタログを分ける。以下の探索/診断をHybrid比較版の根拠とし、Test最大値から選び直さない
- [Content4条件](../data/experiments/content/sensitivity/result.json): IDFあり/log_countの4精度平均が最大。IDF・集約の効果とタグ依存の限界を分ける
- [BM25 75条件](../data/experiments/bm25/grid/result.json): n/k1/bで精度・Coverage・Noveltyが競合し、指標ごとの最大条件が一致しない
- [ALS因子数・感度](../data/experiments/als/paper/result.json) / [ItemCF類似度・近傍数](../data/experiments/itemcf/sensitivity/result.json) / [EASE正則化](../data/experiments/ease/sensitivity/result.json) / [BPR学習率・正則化](../data/experiments/bpr/sensitivity/result.json)
- [保存上位20曲の相補性](../data/experiments/comparison/content-bm25/result.json): union増分は最大40曲の候補診断で、融合Top20の実測ではない。Content/MMR/ILDの特徴依存と、ILD共通母集団の差を保持する

## HybridのValidation

- [実験JSON](../configs/hybrid.json) / [凍結設定](../data/experiments/hybrid/validation/config.json) / [結果と出典](../data/experiments/hybrid/validation/result.json) / [全K・4観点CSV](../data/experiments/hybrid/validation/metrics.csv) / [監査](../data/provenance/cleanup-20261025T122738Z/hybrid-verification.json)
- 全119,133人・45,888曲・10条件/18実行。以下は90日/K20/重み1、新規精度18,601人、再聴19,268人。新規共通ILDはEASE/BPR群85,254人、BM25/Content群117,386人で、群間の直接順位付けはしない

| 条件 | 新規Binary NDCG | 新規Recall (%) | 再聴Graded NDCG | Repeat Recall (%) | 新規共通ILD |
| --- | ---: | ---: | ---: | ---: | ---: |
| EASE | 0.034995 | 2.8675 | 0.105796 | 6.3716 | 0.696694 |
| BPR | 0.027737 | 1.9066 | 0.100527 | 6.7586 | 0.565017 |
| EASE+BPR RRF | 0.037145 | 2.7644 | 0.111746 | 6.8191 | 0.594414 |
| EASE+BPR MMR0.8 | 0.036621 | 2.8262 | 0.112624 | 7.0494 | 0.669544 |
| EASE+BPR MMR0.5 | 0.032336 | 2.4316 | 0.105656 | 7.0513 | 0.863594 |
| BM25 | 0.028050 | 1.9910 | 0.082395 | 5.1194 | 0.585371 |
| Content | 0.009525 | 0.6815 | 0.073027 | 5.1466 | 0.246014 |
| BM25+Content RRF | 0.022110 | 1.6013 | 0.084002 | 5.3397 | 0.414696 |
| BM25+Content MMR0.8 | 0.024665 | 1.7852 | 0.085696 | 5.5089 | 0.530392 |
| BM25+Content MMR0.5 | 0.024931 | 1.8514 | 0.084267 | 5.7866 | 0.751661 |

- 候補統合: 新規pool RecallはEASE100/BPR100のunion14.0415%、EASE200は14.8323%、差−0.7908ポイント[−0.9757,−0.6040]。BM25/Contentのunion8.2345%もBM25200の10.4294%を下回る。単独100からの増加を融合固有の改善と呼ばない。[等取得枠と対応区間](../data/experiments/hybrid/validation/pool.csv)
- 順位: EASE+BPR RRF−EASEの新規NDCG差+0.002150[+0.001226,+0.003156]、再聴NDCG差+0.005950[+0.004790,+0.007177]。新規Recall差−0.1030ポイント[−0.2264,+0.0282]は0を含む。BM25+Content RRFの新規NDCGはBM25より低い。[対応差](../data/experiments/hybrid/validation/paired.csv)
- 多様性: RRF→MMR0.8→0.5で共通ILDは上昇する。EASE/BPRの新規Coverageは92.13→92.62→91.71%、Noveltyは6.093→6.190→6.491bit。BM25/Contentは53.67→55.73→59.42%、5.581→5.395→5.192bitで、ILDとNoveltyは同じ方向に動かない。[共通ILD](../data/experiments/hybrid/validation/ild.csv)
- 尺度: EASE/BPR seed42のλ0.5で選択時関連度項平均0.2955、多様性項0.1611。関連度0〜1化だけでは両項は等尺度にならず、λをそのまま効果の割合と読まない。各実行JSONにraw RRF・cosine・項分布を保持する
- 効率: EASE/BPRのRRF融合約4.50秒、MMR再順位約61秒/全238,266要求。取得は診断用top200込みで、取得100だけの費用・単発応答時間は未測定。API料金等のnullを保持する
- 比較版と主タスク/主指標は未選択、Hybridの最終Testは未実行。既存Test確認後のValidation比較であり、タグ空間の多様性から満足度や未見Testでの優位を結論づけない
