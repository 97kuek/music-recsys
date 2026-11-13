# 推薦の評価結果と考察

## 全体の結論

| 問い | 現時点で言えること | 残る制約 |
| --- | --- | --- |
| 単独方式で何が変わるか | [遷移量の正規化](transition-normalization.md)で新規精度・多様性が未補正より改善し、固定条件の後半期間でも再現。[時間減衰](history-combination.md#時間減衰の後半期間)は再聴の強い対照 | 強い正規化や正規化後の等重みEASE融合は追加改善なし。未使用期間の独立確認・オンライン効果は未確認 |
| 融合に意味があるか | EASE+BPRのRRFはNDCG・再聴と低人気帯の露出に利点がある | 新規Recallの改善を一律には示さず、BM25+Contentにも一般化できない |
| 候補を増やせばよいか | unionは単独100曲から広がるが、等取得枠の単独200曲を上回らない | poolの上限診断と最終TopKの品質は別 |
| MMRは何を改善するか | タグ空間のILDを高め、精度との調整幅を作る | λ0.5は新規精度を下げ、追加計算も必要。主観的多様性は未測定 |
| 個人別の選択は有効か | [ユーザー分離のValidation](personal-selection.md)で旧4候補内では新規NDCG+2.49%、新規Recall+4.30%、Repeat Recall+2.99% | [本人履歴2方式の選択](history-selection.md)は全員組合せ固定となり追加改善0。LLM・独立Testは未実施 |
| どこまで信頼できるか | 全員評価・対応差・共通ILD・出典照合を保存し、GPU疎行列集計を修正した | 既見Test、静的タグの時点可用性、GPU再現差、正解なし・特徴なしの対象差が残る |

- 主タスク・主指標・採用版は未選択。以下の数値は条件付きの比較結果であり、単一の勝者を決めない
- 現在のHybrid比較には修正版Validation/Testを使う。旧版の層別診断・段階費用は処理版と測定範囲を分けて読む

## 比較仕様と原本

- Train＋Validationの227,677,541再生・119,133人・53,336曲で再学習し、2018-09-21以降のTestを評価する
- Testの主表示は90日・K20・日数重み1。新規精度14,069人、再聴精度14,594人の等重み平均。正解なし105,064人／104,539人を精度平均から除外する
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

- 結果: λ2000は比較した3条件の4精度平均が最大。2000−500の新規NDCG差は+0.001620、95%区間[+0.001136,+0.002113]
- 同じ比較の新規Recall・Repeat Recall差の区間は0を含む。正則化を強めるほどCoverageとNoveltyは低下する
- 解釈と限界: 順位精度と露出範囲にトレードオフがある。λ2000は探索端であり、全域の最適値とはいえない。人気度の寄与も未分離
- 効率: 学習約113.3秒、評価135.6〜136.9秒、GPU128人バッチの順位取得0.237〜0.238ms/要求。単発応答時間ではない
- FP32 Choleskyの計画ピーク約22.96GiBと学習後占有約11.26GiBを区別する。[新規NDCG](../data/experiments/ease/test/binary_ndcg_k.png) / [Coverage](../data/experiments/ease/test/catalog_coverage_new_points.png)

## Content: タグの希少性と履歴の重み付け

- 学習カタログ内のタグIDFと正規化タグベクトルを使い、履歴をbinaryまたはlog(1＋再生回数)で集約したプロファイルとのcosineで推薦する
- IDFあり/log_countの主表示は新規NDCG 0.009385・Recall 0.5727%、再聴NDCG 0.070573・Repeat Recall 4.3321%。全4条件は冒頭の原本を参照
- 結果: IDFあり/log_countの4精度平均が最大。IDFありでlog_count−binaryの新規Recall差は+0.0661ポイント[+0.0260,+0.1091]
- IDFは新規NoveltyとILDを高めるがCoverageを下げる。再生回数Popularityより新規Recallは高く、NDCGは低い
- 解釈と限界: 頻出曲と希少タグの強調が嗜好の識別に寄与した可能性がある。要因間の交互作用は未検定。再聴では履歴曲自身もプロファイルへ寄与する
- 効率: 学習61.2〜62.0秒、GPU評価143.6〜146.5秒、順位取得約0.305ms/要求。[新規Recall](../data/experiments/content/test/recall_points.png) / [Novelty](../data/experiments/content/test/novelty_new_points.png)

## BM25: 近傍数と重み付けの効果を分ける

- 曲×ユーザーの再生回数行列へBM25重みを付け、曲間内積の上位n近傍を使う。推論ではbinary履歴を使い、Contentのタグは使わない。基準5条件はk1/b固定、追加4条件はValidation由来
- 基準n20の主表示は新規NDCG 0.025275・Recall 1.5191%、再聴NDCG 0.083549・Repeat Recall 4.4139%。基準5条件とValidation由来4条件の全値は冒頭の原本を参照
- 結果: 基準n200−10は新規Recall−0.1870ポイント[−0.3067,−0.0694]、Repeat Recall+0.7710[+0.6309,+0.9132]
- n20/b0.75でk1を1.2→0.3へ下げると新規NDCGとRepeat Recallが改善する一方、新規Coverageは38.9831%→31.2472%へ低下する
- 解釈と限界: 共起を広く拾うことが再聴に寄与した可能性がある。人気度の効果は未分離。n・k1・bを同時に変えた比較を、nだけの効果とは呼ばない
- 効率: 学習104.0〜104.3秒、評価93.3〜102.4秒、順位取得0.116〜0.141ms/要求。[近傍数と新規Recall](../data/experiments/bm25/test/recall_neighbors_points.png) / [Coverage](../data/experiments/bm25/test/catalog_coverage_new_neighbors_points.png)

## BPR: 学習回数と因子数を分けて比較する

- 二値履歴・項目bias付きのGPU FP32 SGD。正例・負例はCSR要素から抽出し、観測済みの負例はskipする。カタログ一様負例とは異なり、GPUは同seedでもbitwise一致を保証しない
- BPRの数値は3seed平均。128因子でepochを変える系列と、500epochで因子数を変える系列であり、全因子数×全epochの実験ではない
- 128因子/500epochの主表示は新規NDCG 0.028447・Recall 1.5158%、再聴NDCG 0.106019・Repeat Recall 5.9054%。7条件×3seedの全値は冒頭の原本を参照
- 結果: 128因子の500−30epochは新規Recall+0.3141ポイント[+0.2454,+0.3804]、Repeat Recall+0.4604[+0.3413,+0.5641]
- 500−200epochでは両Recallと新規NDCGの差の区間が0を含む。既聴NDCG差は+0.001043[+0.000507,+0.001553]
- 解釈: 後半のRecallの伸びは小さい一方、既聴の順位品質には改善が残る。学習秒は200→500で約1.81倍となり、全指標の収束や200epochの採用をこのTestから確定しない
- 因子数: 500epochでは32→256で4精度の平均が上がる。256は探索端。学習回数と表現容量の効果を同一視せず、モデル構造だけの優劣へ帰属させない
- 効率: 学習71.8〜396.2秒、評価100.7〜101.5秒、順位取得約0.108〜0.110ms/要求。[epochとRecall](../data/experiments/bpr/test/recall_epochs_points.png) / [因子数とNovelty](../data/experiments/bpr/test/novelty_new_factors_points.png)

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

### 結論と適用範囲

- このValidationでは、多段化は一律の精度改善ではなく、順位精度・低人気曲への露出・リスト内多様性を調整する手段となる
- 候補数を増やすだけの優位は確認できない。EASE+BPRのRRFは新規/再聴NDCGとRepeat Recallを改善するが、新規Recallの差の区間は0を含む
- MMRは追加費用を伴ってタグ空間のILDを高める。λ0.5では新規精度の低下も大きく、精度と多様性の優先度なしに採用条件を決めない
- 主タスク・主指標・採用版は未選択。[修正版Hybrid Test](#hybridのtest)は既存Test確認後の追加比較であり、完全未見の検証とは呼ばない

### 品質・候補統合・層別結果

- [実験JSON](../configs/hybrid/base.json) / [凍結設定](../data/experiments/hybrid/validation/config.json) / [結果と出典](../data/experiments/hybrid/validation/result.json) / [全K・4観点CSV](../data/experiments/hybrid/validation/metrics.csv) / [監査](../data/provenance/cleanup-20261025T122738Z/hybrid-verification.json)
- 全119,133人・45,888曲・10条件/18実行。以下は90日/K20/重み1、新規精度18,601人、再聴19,268人。新規共通ILDはEASE/BPR群85,254人、BM25/Content群117,386人で、群間の直接順位付けはしない
- 以下の層別・候補診断は旧Validationに基づく。旧10条件の数値は上記CSV、現行の品質・費用は[修正版原本](../data/experiments/hybrid/validation-corrected/result.json)を参照する
- 候補統合: 新規pool RecallはEASE100/BPR100のunion14.0415%、EASE200は14.8323%。差−0.7908ポイント[−0.9757,−0.6040]
- BM25/ContentのunionもBM25200を下回る。単独100からの増加を融合固有の改善とは呼ばない。[等取得枠の対応差](../data/experiments/hybrid/validation/pool.csv)
- 順位: RRF−EASEの新規NDCG差は+0.002150[+0.001226,+0.003156]、Repeat Recall差は+0.4475ポイント[+0.3251,+0.5762]
- 新規Recall差は−0.1030ポイント[−0.2264,+0.0282]で0を含む。BM25+ContentのRRFはBM25より新規NDCGが低い
- [同一union対照](../data/experiments/hybrid/validation/union-control/result.json)は全員・3seedのpool hashと順位を照合。[全228件のRecall再集計](../data/experiments/hybrid/validation/union-control/paired.csv)と保存値の差は最大2.78e-16
- 多様性: EASE→RRFで新規Coverage73.17→92.13%、Novelty5.528→6.093bit、共通ILD0.696694→0.594414。露出とリスト内多様性は別の量
- RRF→MMR0.8→0.5で共通ILDは上昇する。EASE/BPRのNoveltyは上がり、BM25/Contentでは下がる。[共通ILD原本](../data/experiments/hybrid/validation/ild.csv)
- 尺度: EASE/BPR seed42のλ0.5で選択時関連度項平均0.2955、多様性項0.1611。関連度0〜1化だけでは両項は等尺度にならず、λをそのまま効果の割合と読まない。各実行JSONにraw RRF・cosine・項分布を保持する
- [層別候補Recall](../data/experiments/hybrid/validation/pool-strata/metrics.csv): 新規union−EASE200は人気Q1/Q2/Q3/Q4で+4.7686/+3.1303/−0.2992/−5.0104ポイント
- 人気帯は学習聴取人数157/493/1,140人で区切る。帯ごとの正解保有人数が異なり、差の単純平均は全体差にならない。[人数・出典](../data/experiments/hybrid/validation/pool-strata/result.json)
- [層別TopK](../data/experiments/hybrid/validation/union-control/strata.csv): 新規Recall@20のRRF−EASE差はQ1/Q2/Q3/Q4で+0.5790/+1.0515/+0.6431/−1.1905ポイント。各対応区間は0を含まない
- 低人気帯の増分はTop20にも残る。ただしBPR固有候補の的中分は、失った的中を差し引く純増とは異なる。[全Kの差](../data/experiments/hybrid/validation/union-control/new_popularity_diff.png)
- 区間は固定3seedのユーザー平均に対する探索的bootstrap。人気帯と履歴量は別々の層別で、個人別の選択ルールを証明する分析ではない

### GPU段階費用

- [実行条件・出典・再現性](../data/experiments/hybrid/validation/efficiency/result.json) / [全反復CSV](../data/experiments/hybrid/validation/efficiency/metrics.csv) / [段階費用の図](../data/experiments/hybrid/validation/efficiency/stage_seconds.png)
- RTX 3090、64人バッチ、全119,133人×新規/再聴の238,266要求、BPRの3seed×3反復。初回反復を除く6実行の中央値と最小〜最大を示す。範囲は信頼区間ではない
- 旧経路の全要求あたり中央値は、EASE全曲スコア54.540秒、union整列1.476秒、RRF3.651秒、MMR追加約58秒。各反復と範囲は上記CSVを参照する
- 保存候補を使い、各段階の前後でGPU同期する。候補取得・disk IO・入力検証・描画・API往復は含めず、単発オンライン応答時間へ読み替えない
- EASEはFP32 Choleskyで再学習し、学習90.9秒を別記録。全曲の行列積後にunionを整列する実装であり、候補曲だけの計算に最適化した場合の費用ではない

### 再現性と数値修正の限界

- [段階費用の順位差](../data/experiments/hybrid/validation/efficiency/ranking_differences.parquet): 延べ8,577,576リスト中138件が保存Top20と不一致。EASE再学習134件・MMR0.5が4件で、RRF/MMR0.8は一致。反復込みの件数であり人数ではない
- EASEの差は40 user/task、うち12件で曲集合も変化。保存候補スコア差は最大約1.67e-6。[独立診断](../data/provenance/hybrid-timing-mmr-difference-20261106T2313Z/reproduction.json)ではMMRのGPU64人バッチだけで順序差が生じ、全差を丸めと断定できない
- [記録済み138差の精度影響](../data/provenance/hybrid-architecture-audit-20261107/accuracy_impact.json): EASEの全体平均差は最大約1.11e-5、90日/K20/重み1では約2.95e-7。MMR0.5の精度平均は不変。将来の再実行差や多様性への上限保証ではない
- [GPU段階照合](../data/provenance/hybrid-architecture-audit-20261107/gpu_trace.json): 要素積はCPUと一致しても行合計のcosine差は最大約0.87。行独立のFP64補償和へ修正し、MMR・ILD・Content正規化へ適用。旧品質・費用を修正版の測定値としない
- [保存候補固定の全員照合](../data/provenance/hybrid-architecture-audit-20261107/full-population/result.json): 119,133人・12条件・2,859,192リストでRRF差0、MMR差40。[精度平均差](../data/provenance/hybrid-architecture-audit-20261107/full-population/accuracy_impact.json)は全K/窓で最大約2.08e-5。Content再取得を含まない診断
- [ILD全件照合](../data/provenance/hybrid-numeric-validation-20261109/ild/result.json): 18実行・4,288,788リスト・K2〜20でGPU/CPU差は最大約2.44e-15。旧保存ILDとの差は約3.22e-15、順位修正後は約3.36e-6
- [Content再取得](../data/provenance/hybrid-numeric-validation-20261109/content/result.json): 正規化のCPU相対差約1.67e-15、旧共通候補スコア差約6.66e-16でもTop20は新規1,083・再聴27件、曲集合は計39件変化。[精度平均差](../data/provenance/hybrid-numeric-validation-20261109/content-accuracy/accuracy_impact.json)は最大約8.75e-6。微小スコア差と順位一致を区別する
- [Content融合照合](../data/provenance/hybrid-numeric-validation-20261109/content-fusion/result.json): RRF/MMR0.8/MMR0.5は592/436/56リスト変化。[精度](../data/provenance/hybrid-numeric-validation-20261109/content-fusion/accuracy_impact.json)と[多様性](../data/provenance/hybrid-numeric-validation-20261109/diversity-impact/result.json)の影響を分離。部分診断の共通ILD・効率は未再測定
- [修正版Validation原本](../data/experiments/hybrid/validation-corrected/result.json)は10条件・18実行・全119,133人の4観点・全K・共通ILD・費用を保持。[全件検証](../data/provenance/hybrid-validation-corrected-20261109/verification.json)を参照。EASE/RRF/MMR0.8/MMR0.5のGPUバッチ償却は0.183/0.287/0.508/0.507ms/要求。旧段階費用・API・単発応答時間と区別する

## HybridのTest

- [修正版の原本](../data/experiments/hybrid/test/result.json)は固定10条件・18実行、Train＋Validationの227,677,541再生・119,133人・53,336曲で全ソースを再学習した結果。[全件検証](../data/provenance/hybrid-test-corrected-20261109/verification.json)で4観点・K2〜20・分布、11,286項目のスコア平均/分母、同一pool、出典、54枚のPNGを確認。中断時の成果物と処理版は[出典記録](../data/provenance/hybrid-test-corrected-20261109/snapshot.json)に保持し、Testによる採用版・主指標の選択は行わない

| 固定条件 | 新規Binary NDCG | 新規Recall (%) | 再聴Graded NDCG | Repeat Recall (%) | 新規共通ILD |
| --- | ---: | ---: | ---: | ---: | ---: |
| EASE | 0.034292 | 2.3103 | 0.111056 | 5.6310 | 0.696670 |
| EASE+BPR RRF | 0.037679 | 2.2563 | 0.120026 | 6.0936 | 0.593796 |
| EASE+BPR MMR0.8 | 0.037132 | 2.2657 | 0.120262 | 6.2468 | 0.668787 |
| EASE+BPR MMR0.5 | 0.033573 | 2.0567 | 0.110984 | 5.9991 | 0.863036 |

- [4観点・全条件CSV](../data/experiments/hybrid/test/metrics.csv) / [対応区間](../data/experiments/hybrid/test/paired.csv) / [共通ILD](../data/experiments/hybrid/test/ild.csv)。主表示条件・精度の分母は冒頭のTest仕様と同じ。表の大小だけを有意差と呼ばない
- RRFはEASEよりNDCGとRepeat Recallの平均が高い一方、新規Recallは低い。新規Coverageは66.28→88.83%、Noveltyは5.467→6.036bit、共通ILDは低下する
- MMR0.5はRRFより共通ILDを高めるが、4精度平均は低下する。ValidationのRepeat Recallと異なる傾向もあり、強い多様化が再聴にも常に有利とはいえない
- BM25+Content RRFの新規NDCGは0.019751でBM25の0.025275を下回る。異質な方式の融合自体を改善の根拠にしない。単独方式Testの旧Content値と修正版値も混ぜない

## 固定4群とタスク別ルール

- [ローカル比較原本](../data/experiments/hybrid/validation-corrected/selector-local/result.json)は119,133人×2タスク×5群、1,191,330選択・失敗0件。4固定群と `new → EASE / repeat → RRF` を比較し、外部APIは未実行
- ルールは新規でEASE、再聴でRRFの保存順位・品質と厳密に同じ。個人に応じた改善を示す結果ではなく、選択と集計の接続確認である。Validation後の探索的な規則で、主指標や採用版を確定しない
- 4観点・全K・全窓・日数重み・対応差・分布は[CSV](../data/experiments/hybrid/validation-corrected/selector-local/metrics.csv)に保持。選択分岐の時間は測定済みだが、推薦応答時間・API料金・tokenは未測定null
- 個人別ルールの交差評価・個別スコア/露出集合/共通ILDの再集計と考察は[個人別選択](personal-selection.md)を参照。LLM実験の入力・費用・失敗の契約は[選択器比較](selector-study.md)、残る判断は[TODO](todo.md)
