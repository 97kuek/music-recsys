# listening-v2の探索とNotebook

再生回数・再生日数を使う[listening-v2](listening-evaluation.md)を、共通の[探索フロー](experiment-workflow.md)と01→02→03で扱う。既存のBinary JSON実験はそのまま使用できる。v2はTOMLで明示する。

## 最初に操作を確認する

以下は合成データ専用で、実データの性能や最適値を示さない。同期済み仮想環境の`python`、または`uv run --locked --group data --group tracking python`を使用する。リポジトリのルートで実行する。

```bash
python examples/toy/create_listening.py
export MUSIC_RECSYS_EXPERIMENT_CONFIG="$PWD/configs/workflow-listening-itemcf.toml"
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" smoke
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" search
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" select
```

同じ環境変数を引き継いでJupyterを起動する。

1. **01_data**：選択したValidationの観測分布、new/repeat別の評価可能性を確認する。既存のMusic4All全量分析は参考データとして区別される。
2. **02_analysis**：Setupでprotocol・主タスク・主指標・期間・日数重みを確認する。探索結果と最良／次点、K曲線、履歴量別性能を見る。
3. **03_recommendation**：`selection.json`からモデル・パラメータ・seed・タスクを読み込む。主タスクがrepeatなら既聴曲、discovery / retention_proxyなら未聴曲の推薦を表示する。Testの正解を読まず、Validationの学習履歴で確認する。
4. 03で確認した内容を記録して設定を固定し、Train＋Validationで再学習してTestを実行する。

```bash
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" review --accept --note "03で実際に確認した内容"
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" freeze
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" test
```

02のFinal Comparisonで固定済みTest結果を確認する。同じ工程を`configs/workflow-listening-toy.toml`にも実施すれば、実装済みPopularityとの比較ができる。未実行モデルの結果は表示しない。合成bundle生成は既存出力への上書きを拒否する。新しい例を作る場合は`--output`とTOMLの入力・出力先を別の場所にする。

## 実験条件を固定する

```toml
[experiment]
name = "my-listening-study"
protocol = "listening-v2"
validation_dataset = "../data/benchmarks/my-split/validation"
test_dataset = "../data/benchmarks/my-split/test"
output = "../data/experiments/my-listening-study"

[evaluation]
window_days = [30, 90, 180]
primary_window_days = 90
day_weights = [0.0, 0.5, 1.0, 2.0]
primary_day_weight = 1.0

[selection]
task = "repeat"
metric = "graded_ndcg"
k = 10
direction = "maximize"
```

`[model]`・`[search]`・`[tracking]`は共通フローと同じ。Kは既定1〜20、主Kは`selection.k`に集約する。`evaluation.primary_k`を併記する場合は一致が必要。例の主タスクは操作確認用であり、実研究では目的から事前に決める。

| selection.task | 使用可能な主指標 | 03の推薦対象 |
| --- | --- | --- |
| discovery | binary_ndcg / recall | 未聴曲 |
| retention_proxy | graded_ndcg | 未聴曲（discoveryと同じランキング） |
| repeat | graded_ndcg / repeat_recall | 既聴曲 |

主指標未指定の診断探索は可能だが、最良選択・固定・Testはできない。`graded_ndcg`だけの指定は拒否する。タスク・指標・主期間・重み・データ規模を後から変更する場合は別の出力先で新しい実験を作る。期間・重みの感度分析結果から主条件を事後選択しない。

入力はexport済みmanifest付きbundle。ValidationはTrainとValidationの源ファイル記録、Testは同じTrain・ValidationにTestを追加した源ファイル記録を必要とする。manifest・全Parquetのhashと型・行数・観測事実を検証する。Testでは元分割の一致、Train＋Validationの学習件数と既知観測の包含も照合する。探索と03にはTest観測を渡さない。固定時にTest入力全体のhashを記録し、固定後の改変を拒否する。

この照合はexport済みbundleの整合性検査であり、元イベントからの完全な再構成ではない。観測窓外やcoldイベントの各ペアへの割当てまで検証するには、元分割ファイルの再読込みが必要となる。共通exporterで同じ保存済みSplitから生成したbundleを使用する。

## 表示と保存

- `results.json`は既存v2形式、`curves.json`は全タスク・期間・K・日数重みのlong形式の集計と要求別指標を保存する。探索表の主指標は事前指定の条件だけを参照する。`study.json`・`selection.json`・確認・固定artifactは既存と同じ動線で使用する。
- 02のK曲線は主期間・主日数重みに限定し、未聴曲のBinary / Graded / Recallと既聴曲のGraded / Repeat Recallを別パネルで表示する。各ユーザーの値をseed平均してから、ユーザー単位bootstrapで95% CIを求める。ユーザーが2人未満ならCIは未測定。
- 期間・日数重みの補助条件も`selected_curves(config)`のlong形式に残す。重複したbootstrap計算を避け、補助条件の平均・評価人数は計算するがCIは求めない。主条件と混ぜて平均しない。
- v2のILD・Novelty・Catalog Coverageは現在未測定。v1とは候補・不正順位の扱いが違うため、定義を決めて追加する。Latency P50/P95・Training Timeは実測値を表示する。Tokens / API Cost / Calls / Memoryはモデルが実測値を返した場合だけ集計する。
- v1/v2や、異なる期間・gain・データ・コード条件のモデル結果を直接比較しない。不一致は02で案内する。v2を選んだ状態で旧Binaryの参考結果へ暗黙に切り替えない。

`tracking.enabled=true`なら各試行をMLflowに保存する。モデル呼出しは各ユーザーのnew/repeatごとに一度で、窓・K・重みや表示の再読込みでは呼出しを増やさない。成功済み同条件試行を再利用する。生の履歴・要求・観測値はローカル成果物に保持する。

## 検証記録（2026-10-15）

全テスト286件成功・3件スキップ。v2の01/02/03とTest後02をfresh kernelで実行し、実MLflow SQLiteの成功・失敗・再利用を確認した。合成データのItemCF・PopularityでCLI全工程と二モデルの最終比較を実行し、観測分布・Grid・Validation・Testの図を確認した。静的解析・配布物ビルドも成功。今回、実DB・大規模生成の任意試験は再実行していない。実データの最適設定や全量推薦性能を示す結果ではない。
