# 再生観測を使う評価（listening-v2）

既存のBinary未聴曲評価を維持し、v2は明示指定で使用する。GroundTruthには再生回数・再生日数を保存し、gainはEvaluatorで`log2(1+n)+λ*log2(1+d)`として計算する。主条件は90日・K=10・λ=1、期間感度30/180日、K=1〜20、λ=0/0.5/2。元の学習期間と分割境界を維持する。

## 生成と実行

保存済みSplitから新しいbundleを作る。DBは読込みのみで、既存snapshot・split・成果物を変更しない。

```bash
uv run --locked --group db --group data python -m music_recsys.temporal_cli export --split-id <保存済みSplitのUUID> --phase validation --protocol listening-v2 --output data/benchmarks/music4all-listening/validation
uv run --locked --group data --group tracking python -m music_recsys.evaluation.benchmark --config configs/music4all-listening-validation.toml --output data/experiments/listening-validation
```

出力は`learning.parquet`・`observations.parquet`・`manifest.json`。学習／評価をユーザーhashの32分割で処理し、DuckDBの既定は2GB・2スレッド。最後に行順・writer設定を固定して書き、内容hash・型・一意性・warm条件・読戻しを検証した後でmanifestを公開する。既存出力への上書きは拒否する。ValidationではTestのイベントを読まない。Test生成は`--phase test`と別の出力先を使い、学習はTrain＋Validation全期間となる。

全量GroundTruth生成とモデル実行を分ける。モデル実行は実Parquetから学習ユーザー数・再生回数を数え、2,000ユーザー・500万イベントを超えた場合はPythonへ展開する前に拒否する。小規模なSnapshot / Splitを別の実験条件として準備する。上限超過を暗黙にサンプリングしない。

`benchmark.model="itemcf"`と`[benchmark.parameters]`で実装済みItemCFも実行できる。モデルへ渡すのは学習情報・履歴・候補だけで、将来の回数・日数は渡さない。

## 指標と保存結果

| task | 候補 | 指標 |
| --- | --- | --- |
| discovery | 学習カタログの本人未聴曲 | Binary NDCG、Recall |
| retention_proxy | discoveryと同じランキング | Graded NDCG |
| repeat | 本人の学習期間の既聴曲 | Graded NDCG、Repeat Recall |

ユーザーごとにnew / repeatを各1回だけ生成する。空候補では生成を省略するが、ユーザー自体は残す。窓・K・λを増やしてもモデル呼出数を増やさない。各条件の正解保有ユーザーを等重みで集計し、正解なしはnull、推薦失敗で正解ありは0点とする。

先頭20位置を固定し、不正ID・重複の位置は0点とする。後続曲を繰り上げない。不足数・不正数・重複数・超過数はK別diagnosticsへ保存する。IDCGは候補内の全正解から計算する。

結果は`results.json`（生ランキング・要求別指標・診断）、`metrics.csv`（条件別の集計）、`summary.md`（主条件）。CSVの比較キーは`task / window_days / k / day_weight / metric`。Binaryのday_weightは空欄で、λごとに重複保存しない。MLflowには集計だけを送り、生ID・履歴・観測値はローカルに保持する。

v1とv2では候補・gain・不正出力の位置処理が異なるため、同条件として比較しない。単独の結果CSVに加え、共通RunnerのGrid探索・選択・固定・Testと01〜03のNotebookで扱える（[v2探索の手順](listening-workflow.md)）。既存v1の結果Readerとv2の条件別Readerを分け、異なるprotocolの比較を拒否する。Testによるパラメータ探索は行わず、Validationで条件を確定してから最終評価する。

## 将来モデルの実測値

モデルアダプタは`RecommendationResponse(..., usage=RecommendationUsage(...))`へ、取得できた実測値だけを渡す。

- `input_tokens` / `output_tokens`、`llm_calls` / `tool_calls`：非負の整数。
- `api_cost_usd`：実際に確認できたUSD料金。単価からの自動推計はしない。
- `peak_memory_bytes`：推薦要求内で測定したメモリの最大bytes。学習時のピークは別途測定が必要。

未取得はNone。ある項目を一部の要求でしか取得できない場合、総量を欠測分0として計算しない。総量はnullとし、取得件数を`usage.measured_requests`に保存する。メモリは全生成要求が計測済みの場合の最大、その他は合計。失敗後の使用量が分かるアダプタは、例外を投げる代わりに`status="error"`のResponseへusageを含める。K・期間・λを変えても使用量を重複計上しない。

LLM・Agent自体や課金API呼出しは追加していない。Popularity・ItemCFの未測定項目も自動で0とは扱わない。

## 検証記録（2026-10-15）

通常テスト268件、隔離DB128件が成功。既存の時系列処理と新しいv2生成はそれぞれ600万件・DuckDB 128MB制限の合成試験を通過した。ItemCFはSmoke・実Grid・選択・03のfresh kernel・固定・Test・02のfresh kernelを確認した。実Music4All全量での推薦精度・最適パラメータ・プロセス全体のRSSは測定していない。
