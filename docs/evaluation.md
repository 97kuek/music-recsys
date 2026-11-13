# 評価指標と比較条件

## オフラインの母集団とタスク

- 母集団は学習履歴のある全ユーザー。人数を暗黙に制限せず、未来に活動した人だけを推薦対象にしない
- Train/Validation/Testは時系列分割。ValidationはTrain、最終Testは固定後のTrain＋Validationから学習する
- 推薦器へ将来正解・将来再生回数・再生日数を渡さない。特徴は版とhashを固定し、付与日時が不明な静的タグの時点可用性は別の制約として示す
- newの候補は学習中に未聴の曲、repeatの候補は学習中に聴いた曲。各候補のうち評価期間に再生された曲を正解とし、候補生成へ将来観測を使わない
- 全方式で同じ期間・カタログ・許可候補・正解・同点規則を照合する。入力変換・solver・精度・seedの差は保持する
- 正解なしは精度平均から除外し、`n_evaluated`・`n_excluded`を表示する。推薦生成と多様性の母集団は全学習ユーザー
- 表示基準は90日・K20・日数重み1。選択用主タスク・主指標を確定する設定ではない
- 観測窓30/90/180日、K=2〜20、日数重み0/0.5/1/2を保存する。効率性以外のK感度を省略しない

```mermaid
flowchart LR
    H[学習履歴] --> P[許可候補と推薦順位]
    O[将来観測 / Evaluatorだけ] --> S[タスク別正解とgain]
    P --> M[固定指標 / 個別Parquet]
    S --> M
    M --> U[等重みユーザー平均 / 分母・除外数]
    U --> C[対応差 / 4観点 / K感度と分布]
```

## 固定する4観点

| 観点 | 主な指標 | 分母・意味 |
| --- | --- | --- |
| 新規発見 | Binary NDCG・Recall | new正解を持つユーザーの等重み平均 |
| 継続的な嗜好 | Graded NDCG・Repeat Recall | repeat正解を持つユーザーの等重み平均 |
| 多様性・発見性 | ILD・Catalog Coverage・Novelty | 全要求、または特徴が有効な要求を明示 |
| 効率性 | レイテンシ・token・API料金・LLM/tool call | 実測した単位・取得範囲・失敗も保持 |

- 未聴Graded NDCGは定着の代理として別表示する。観測期間内の再生であり、長期嗜好の確定ではない
- 選択用の主指標が未指定でも4観点を測定・表示する。異なる指標を勝手に総合点へ混ぜない

## 精度の定義

- `Recall@K=|推薦上位K∩正解|/|正解|`。repeatでは学習曲内の再聴正解を分母にする
- Binary DCGは上位Kの正解曲へ`1/log2(rank+1)`を加算し、全正解から作る理想順位のIDCGで割る
- Graded gainは`log2(1+再生回数)+日数重み*log2(1+再生日数)`。同じ窓・重みの全正解でIDCGを作る
- 候補pool内の正解だけでIDCGやRecall分母を作り直さない
- 短いリストは返した曲の位置を使い、残り位置を的中として埋めない。正解なしはnull
- 再生回数・再生日数は指標とは別に、窓/task別のユーザー・曲ペア分布をCSV・個別図へ保存する

## 多様性・発見性

| 指標 | 定義・有効条件 | 注意点 |
| --- | --- | --- |
| ILD | 上位Kの全曲ペアのcosine距離平均。2曲以上、全曲のタグが存在し非ゼロ | 短いリスト・特徴欠損・ゼロノルムの人数を別記 |
| Coverage | 推薦に現れたユニーク曲数 / taskの許可候補の和集合 | 条件ごとのpool数を全カタログの分母へ置換しない |
| Novelty | 学習ユニーク人数の`-log2((人数+1)/(全学習人数+2))`を曲・要求で平均 | 学習人気度による自己情報量、本人の未知や満足ではない |

- 同じ特徴artifactを全方式に使い、hashが異なるILDを同条件として比較しない
- ILDは欠損を0距離・0類似度へ補わない。元の有効人数と共通有効ユーザー集合での値を分ける
- 共通集合はtask/Kごとに全比較条件で特徴が有効な人の積集合。集合が異なる図を直接順位付けしない
- 現行GPU ILDはFP64の累積ベクトル和による恒等式を使い、独立した曲ペア計算と照合する。旧経路の差と原本保持は[考察](discussion.md)を参照
- Content/MMRと同じタグを使うILDは、そのタグ空間での評価。音響・主観的多様性の独立な検証とはしない

## Hybridの段階別評価

| 問い | 比較 | 指標・判断 |
| --- | --- | --- |
| RQ1 候補統合 | A100・B100・union、等取得枠のA200/B200 | Pool Recall・共有/固有候補・実件数・対応差・取得費用 |
| RQ2 順位融合 | 単独・同じunion上のRRF、可能なら同じunionを単独で再採点 | 固定精度・全K・順位差・融合費用 |
| RQ3 多様性制御 | 同じpool/RRFのλ1/0.8/0.5 | 精度・共通ILD・Coverage・Novelty・追加費用のトレードオフ |

- Pool Recallは`|pool∩正解|/|正解|`。unionが各部分集合以上になること自体は集合包含の性質
- A100＋B100は取得枠200。単独A100との差をRRFだけの効果と呼ばず、A200/B200との対照を添える
- 取得枠が同じでもunique候補数・実計算量は同じとは限らない。取得m・pool件数・段階時間を保存する
- RRF/MMR比較では候補artifact・pool hashを固定する。λ1は完全なidentityとして検査する
- raw/正規化RRF・cosine・重み付き関連度/多様性項の分布を記録する。0〜1化だけで尺度が揃ったと判断しない
- pool内理想順位はoracle上限診断。候補Recallやoracleの増分を、達成した最終TopKの改善と呼ばない

### 保存候補の層別診断

```bash
# 既定は計画表示、--runで保存候補をGPU集計する

.venv/bin/python -m backend.interfaces.cli.hybrid_strata --config configs/hybrid/base.json
.venv/bin/python -m backend.interfaces.cli.hybrid_strata --config configs/hybrid/base.json --run
```

- 履歴量は学習ユニーク曲数、人気度は曲ごとの学習ユニーク人数の四分位。同値は下側の層へまとめ、空の層も保持する
- 履歴層はユーザーを限定、人気帯は正解曲と的中曲を限定する。正解なしを除外し、層の母集団・評価人数・除外人数をCSVへ保存する
- A100・B100・union100・A200・B200を比較。BPRはユーザー単位で3seedを等重み平均してから対応差を取る。区間は未推定で、有意差を示さない
- 30/90/180日はCSV、90日は層別Recallと対応差を別PNGに保存。これは取得枠100/200の候補診断で、順位付きRecall@Kとは異なる
- 4観点・K2〜20・再生回数/日数分布は元のHybrid結果を参照する。モデル学習・再採点・最終Testは開始しない
- 保存先は実験直下の`pool-strata/`。config.json・result.json・metrics.csv・run.log・PNGと個別Parquetを保存し、値を巨大なJSONへ展開しない
- 入力・候補・出典・処理版を照合し、成功時だけ公開。既存出力や`pool-strata.partial/`があれば拒否し、途中結果を上書きしない
- 本番はCUDA/float64で集計し、デバイス・処理時間を記録。テスト用NumPyとの集計順序差は許容誤差で照合する
- この診断の入力検証はDuckDBのメモリ上限16GBを使うため、実行前にホストの空きメモリを確認する。上限は実行条件へ保存し、他の入力検証の既定2GBは維持する

```mermaid
flowchart LR
    L[学習履歴] --> B[同値を分割しない四分位]
    C[保存候補・出典照合] --> R[GPU候補Recall]
    O[Validation正解] --> R
    B --> R
    R --> P[ユーザー対応・seed平均・人数]
    P --> V[再照合後に新規診断を公開]
```

### 同一unionの順位対照

```bash
.venv/bin/python -m backend.interfaces.cli.hybrid_union --config configs/hybrid/base.json
# 計画を確認してから明示実行

.venv/bin/python -m backend.interfaces.cli.hybrid_union --config configs/hybrid/base.json --run
```

- `pool-strata`の成功済み入力検証と出典を照合し、保存候補・推薦の全学習ユーザーと3seedをGPUで検証する。出力は`union-control/`、途中保存は`.partial`とし、どちらも既存時は拒否する
- EASE上位100曲を含むunionを同じEASEスコア・同点規則で並べると、K≤100のTopKはEASE単独と同じ。保存上位200曲のスコア順・同点順、Top20、RRF順位、pool hashを全件照合する。候補外のスコア補完や再学習は行わない
- 精度・多様性・対応差は順位の一致を条件に保存値を参照する。共通ILDは元のEASE/BPR群全条件の共通集合を維持し、この2条件だけの集合へ拡大しない
- 履歴量別・人気帯別のRecall@2〜20を30/90/180日で集計し、3seedをユーザーごとに平均する。人気帯は正解と的中の両方を限定し、全体のRecall分母を候補poolで作り直さない
- `rrf-bpr-only`はEASE上位100曲にない候補からの的中分。同じ帯の全正解を分母とし、RRF内の元の順位を保つ。EASEから失った的中を差し引く純増とは区別する
- 90日/K20の層別値・RRF−EASE差はユーザー対応bootstrap 2,000回・seed42の95%区間を付ける。多重比較補正なしの探索的区間で、履歴量と人気帯を交差した分析ではない
- RRFの既存費用は取得top200込みのバッチ償却値。EASEによるunion再採点の実行時間は未測定nullとし、照合・集計時間から補わない。全値nullの費用図は作らない
- config/result/metrics/run.log、全Kのstrata.csv・paired.csv・ild.csv、個別推薦/層別スコアParquet、1指標1図を保存する。4観点と再生回数/日数分布を表示し、選択・最終Testは実行しない
- `CUDA_PATH=/usr .venv/bin/python -m backend.interfaces.cli.hybrid_timing --config configs/hybrid/base.json --run`は別の明示計測。EASEを同じ学習条件で再構築し、保存候補での全件スコア・union整列・RRF・MMRをGPU同期して3反復する。全員・3seedでRRFのTop20一致を要求し、EASE再学習とMMR再実行の順位差、EASE保存候補スコア差を別記録する。元の品質値を再学習版の品質測定とみなさない。初回とwarm反復を区別し、保存先は`efficiency/`とする。取得・disk IO・オンライン応答時間は段階費用へ含めない

```mermaid
flowchart LR
    C[保存候補・推薦・出典照合] --> V[全員・3seedのpool hashとTop20照合]
    V --> M[順位同値な4観点の保存値]
    V --> R[GPU層別Recall・ユーザー対応区間]
    M --> P[入力と処理版の再照合後に公開]
    R --> P
```

### Hybridの固定条件Test

- `hybrid --config configs/hybrid/base.json --phase test`でValidationと同じ10条件・18実行を計画し、`--run`で明示実行する。主タスクや採用モデルを自動選択しない
- 出力は`data/experiments/hybrid/test/`。全ソースをTestの学習履歴（Train＋Validation）だけで再学習し、同じ重みと候補を単独/RRF/MMRで共有する。Validationの重みや候補をTestへ流用しない
- EASEのメモリ上限は`--ease-memory-budget-mb`で明示し、実行設定へ記録する。53,336曲のRTX 3090実行は24576MiB。正則化・精度・solverは変えず、現在の空きVRAMの検査を維持する
- Validationの設定・結果参照、Test入力hash、処理版を固定し、再開時も一致を要求する。成功済み出力と失敗途中の候補を上書きしない
- 4観点・全K・分布・対応差・共通ILDはValidationと同じ定義。既存の単独方式Testを確認した後の追加比較であり、完全未見の外部検証とは呼ばない
- 保存Testの読込みは`read_result(config, phase="test")`、Notebook用の表は`full_analysis.curves(config, phase="test")`で明示する。既定はValidationで、自動切替しない

## 不確実性と選択

- 同じユーザー・task・窓・重みを対応付けて差を計算する。差の向きはCSV/JSONに記録する
- 基準はユーザーbootstrap2,000回・seed42・95%区間。多数比較の補正なしの区間は探索的と明記する
- 3seedはユーザーごとに平均してからbootstrapし、学習seedの標準偏差を別表示する。ユーザー数を3倍にしない
- seedごとの区間端を平均しない。未測定の区間はnull、単発学習時間へ未測定の誤差棒を付けない
- CIの重なりだけで差を判断せず、対応差の区間を確認する。0を含む差から同等性・効果なしを証明しない
- Validation/Testは時期・カタログ・正解保有人数が異なる。絶対値差だけを過学習・時間変化の原因へ帰属させない
- Testから採用条件を選び直さない。既存Testを見た後の追加探索・固定比較は、その経緯を出典とともに区別する

## 欠測・失敗・効率

- 未取得・未測定・未実行はnull、実測0は0。全値nullの指標はPNGを生成・表示しない
- 候補不足・候補源失敗・不正なID/重複・pool外出力・数値非有限を検査し、失敗を成功人数から黙って除かない
- 失敗した条件の途中ファイルを保持し、成功マーカーを作らない。再開時は出典と処理版を一致させる
- 学習・重み復元・候補取得・cache読込み・融合・再ランキング・指標/描画の時間を区別する
- GPUバッチ償却、CPU単発推論、API往復、ブラウザ表示時間は同じ量ではない
- cacheを使っても元の取得/学習費用を保存し、再利用した重みを新規学習0秒として扱わない
- token・料金・LLM/tool callは実測のみ。非LLMという理由で未計測値を0にしない

## オンラインの集計単位

- 履歴予測とは別に、参加者が提示をどう受け取ったかを測る。[UI・同意・ログ運用](ui.md)を参照
- 曲単位は参加者×session×period/condition×request×item。再生単位にはplayback_idを加える
- event_idの同内容再送を除去し、同一要求のimpression/Likeを重複加点しない
- 分母は実際に提示を確認した曲・リスト。生成しただけの曲を含めない
- 未完了・通信断・非表示・観測間隔欠損・再生可否不明を分け、未観測の非発生を実測0にしない
- rate出力はvalue・numerator・denominator・missing。分母0や未確定対象を含む率はnull

| 指標 | 分子・分母 / 操作 | 欠測・限界 |
| --- | --- | --- |
| Satisfaction | リスト全体の1〜5、任意、1要求1回。有効rating合計 / 回答リスト数 | 未回答null、平均・回答数・未回答数を併記。オンラインの主指標 |
| Like Rate | Likeされた提示済みユニーク曲 / 提示済みユニーク曲 | 観測不十分な非Likeは欠測。Likeなしは不満足の確定でない |
| Play Start Rate | playを確認した曲 / 提示時availableの曲 | 可否不明はnull、unavailable/expiredを分ける |
| Engaged Listen Rate | 累積10秒以上の曲 / 同上 | 感度5/15/20秒、閾値未到達を確定できない対象は欠測 |
| Track Switch Rate | 再生中の別曲移動 / play確認済み再生ID | pause・hidden・error・endedは移動へ変換しない |
| Early Skip Rate | 10秒未満で再生中に別曲へ移動 / 同上 | 10秒ちょうどは含めず、中断を不満足の確定値にしない |
| Discovery Success | Like曲から「初めて知った曲」を任意選択。成功リスト / 有効回答リスト | selected/none/unansweredを区別。noneは0、未回答null |

- listened_secondsは同じplayback_id内の最大値、別ID間は実測秒を合算する。seekした音源位置を試聴秒に加算しない
- list_evaluationで観測を閉じ、同一要求の別ID再評価を拒否する。未送信の操作・最終秒のACKを確認する
- Discoveryの回答率は、Likeがあり質問を提示したリストが分母。学習上の未聴を本人の未知とみなさない
- selection_seconds・inference_seconds・API応答・初回可視時刻を区別し、後方カードのスクロール待ちを分ける
- 新旧ログの測定定義を混ぜない。旧skipや曖昧な曲/リスト評価は診断として保持する
- 最大5人、通常3条件の探索的比較。参加者内平均・対応差・順序/重複曲/休憩を記述し、一般的優位や有意差を断定しない

```bash
.venv/bin/python -m backend.interfaces.cli.online_metrics \
  --input /private/exports/SESSION_ID.json --output /private/metrics-new.json
```

- 読取り専用の集計。元ログ/DB/モデルを変更せず、新規0600出力へ完成後に公開する
