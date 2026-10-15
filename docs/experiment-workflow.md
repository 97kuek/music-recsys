# 検証・パラメータ探索フロー

- ユーザーが指定した検証フローを採用する。既存Binary評価に加え、明示した`listening-v2`では再生回数・日数によるGraded評価を別条件として扱う（[v2の設定と操作](listening-workflow.md)）。Kは主設定10・感度1〜20、ILDは測定する場合2以上。
- 01でデータと評価可能性を確認 → CLIでSmoke → Validation探索 → 02で比較・選択 → 03でValidationの履歴と推薦を観察 → 設定固定 → CLIでTrain＋Validationの再学習とTest → 02で最終比較。

## Notebook

| Notebook | 役割 | 次へ進む条件 |
| --- | --- | --- |
| 01_data | 既存の品質・分割確認、保存済みGroundTruth回数・日数の分布 | 出典・分割・評価可能性が確認できる |
| 02_analysis | Setup、探索結果、選択、感度・トレードオフ、最終比較、次の操作 | Validationで設定を選び、03で確認し、固定する |
| 03_recommendation | 選択artifactからモデル・パラメータ・seedを読み、Validationの学習履歴だけで個別確認 | 明示的に確認済みを記録する。問題があれば02へ戻る |

- 実験設定はTOMLの1か所。NotebookとCLIは同じ設定を読む。Notebookの既定操作は読込みのみで、長時間の探索はCLIへ委譲する。
- 既存保存結果の分析機能と音源の手動試聴を維持する。未実行・未測定は0で補わない。

## 設定と探索

- `[experiment]`に名称、Validation入力、任意のTest入力、出力先、固定するSnapshot / Splitの識別を置く。入力のprovenance.phaseと設定フェーズを照合する。
- `[model]`に実装済みモデル名と固定パラメータ、`[search.grid]`に探索値、seed一覧、Stage A/B/C、試行数・時間予算を置く。Popularityのパラメータは空で、架空の探索パラメータは追加しない。
- seed一覧・Stage・予算は`[search]`、可変値だけを`[search.grid]`に置く。Smokeは`smoke_users`（既定3人）の要求と、対象者＋先頭`smoke_train_users`（既定50人）の学習履歴で動作確認する。カタログは維持する。Smokeは独立したphase/hashを持ち、本探索の性能や最良選択には使用しない。
- Stage Aは可変パラメータ1個以下、BはGrid、Cは同じデータ・モデル条件の親探索を指定して範囲を絞る。Random / Optunaは今回実装しない。
- `[selection]`に指標、K、最大化／最小化を開始前に置く。未指定の診断探索は許すが、自動選択・固定・Testは禁止する。指標を後から変更する場合は別の探索として記録する。
- Smokeに合格してから探索する。成功した同条件・同パラメータ・同seedの試行だけを再利用する。失敗・中断は新しいattemptに残して再試行する。
- 試行の開始前と終了後にatomicなJSONを書き、CLI停止で成功済み結果を失わない。ファイルロックで同じ探索への同時書込みを防ぐ。予算切れや失敗が残る場合は完了扱いにしない。

## 保存と公平性

- 条件の指紋にはデータhash、出典、候補・GroundTruth・Evaluator、生成K=20、コード版、依存lock、選択指標を含める。モデル・パラメータ・seedごとにtrial IDを作る。
- 学習・推薦・評価は共通Runnerへ委譲する。上位20曲を一度だけ取得し、同じランキングからK=1〜20を計算する。要求ごとの値と条件別集計を保存する。
- 主指標はseed間で平均し、全予定seedが成功した設定を比較する。探索が未完了・失敗・条件不一致なら最良設定を確定しない。同点の順位はcanonicalなパラメータ表記で安定させ、seedを選別しない。
- `study.json`（状態・全試行・予算）、各trialの`results.json`・`curves.json`、`selection.json`（候補）、`review.json`（03の確認）、`locked-selection.json`（最終固定）、`final-test.json`（Test実行記録）を保存する。相対パスと内容hashで結び付け、改変・条件不一致を検出する。
- 固定には03の確認記録を必要とし、Test入力のhashも固定する。最終Testは固定済みモデル・パラメータ・全seedだけを使う。Testのphase、Snapshot / Split、Train＋Validationの学習出典と履歴を照合し、Validation探索を呼び出さない。
- Test結果から再選択する機能は設けない。再実行は同じ固定条件の成功結果を再利用する。
- MLflowへ各試行のパラメータ・指標・時間・状態、集計成果物と詳細への参照を保存する。生の要求・ユーザー・GroundTruthを公開成果物として送らない。

## 可視化と案内

- 1変数は折れ線、2変数はヒートマップ、3変数以上は上位表と固定条件付き感度。最良・次点、境界上の最良、差が小さい場合の案内を示す。
- K別曲線、95%ユーザー単位bootstrap、履歴量別性能を共通のsrc関数で作る。seed間のばらつきとユーザー間の区間を区別する。
- Coverageは全要求の推薦可能候補の和集合を分母とする。Noveltyは学習中のユニークユーザー数による人気度をBeta(1,1)で平滑化した自己情報量のユーザー平均。条件定義を保存する。
- ILDは同じ出典の特徴が指定されたときだけcosine距離の平均を計算し、特徴の欠損・空特徴・推薦数不足は未測定とする。Graded、Tokens、API Cost、LLM / Tool Calls、Peak Memoryも未測定ならnullと表示理由を保持する。
- LatencyのP50/P95とTraining Timeは実測する。グラフにはフェーズ、指標、軸、K・seed集約条件を示す。同じ情報のグラフを重複させない。
- 状態から次の作業と戻り先を示す。判断不能、探索中断、条件不一致、失敗、少数ユーザーを正常・最適化完了とみなさない。

## 実装範囲

- 将来のモデルは既存のfactory経由でパラメータ・seedを受け取れるようにする。実装済みモデルはPopularityとItemCF。ItemCFの定義・補完規則は[itemcf.md](itemcf.md)を参照する。
- 探索の1D/2D、多seed、失敗・再開、選択、固定、Test禁止条件はテスト用の合成モデルを使って検証する。実在モデルの測定結果を捏造しない。
- 旧Notebook名の参照・テストを新名へ更新する。既存データ・実験履歴は変更しない。コミット・pushは依頼があるまで行わない。

## 実行手順

最初は`configs/workflow-toy.toml`を使う。これは合成データによる操作確認であり、研究上の性能結果ではない。以下の`python`は`uv run --locked --group data --group tracking python`で実行するか、同期済みの仮想環境を使用する。

```bash
export MUSIC_RECSYS_EXPERIMENT_CONFIG="$PWD/configs/workflow-toy.toml"
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" status
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" smoke
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" search
```

同じ環境変数を設定したプロセスからJupyterを起動し、01→02を開く。02のSetupで出典・探索範囲・主指標・予算と状態を確認し、Search Resultsで試行と実行時間、Model Selectionで最良・次点を比較する。選択はSetupの`ACTION="select"`で明示実行するか、次のCLIで保存する。通常は`ACTION=None`のままにする。

```bash
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" select
```

03は`selection.json`からValidation入力・パラメータ・先頭seedを読み込む。対象ユーザーをSetupで選び、履歴・ランキング・スコア・根拠・特徴・音源を確認する。03の`REVIEW_ACTION="accept"`または`"reject"`を明示して記録する（既定None）。CLIから記録する場合も、実際に03で確認してから実行する。

```bash
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" review --accept --note "Validationの推薦を確認した内容"
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" freeze
python -m music_recsys.experiment_search --config "$MUSIC_RECSYS_EXPERIMENT_CONFIG" test
```

問題があれば`review --no-accept`として02のValidation探索・モデル実装へ戻る。固定後は探索・再選択を禁止する。Testは全seedを固定条件で再学習し、失敗seedだけ再試行できる。成功済みTestの再実行は保存結果を返す。02のFinal Comparisonで比較し、Testを見て探索範囲を変えない。

01・02・03は同じTOMLのValidation入力を確認する。01の既存DB EDAは`MUSIC_RECSYS_SNAPSHOT_ID` / `MUSIC_RECSYS_SPLIT_ID`で選べる補助分析、全量の回数・日数の集計は参考データとして区別して表示する。現在のJSON形式には再生時刻・日別観測値がないため、選択Snapshotの再生日数を推測して補わない。

### 設定の変更と再開

- TOML内のパスはそのTOMLからの相対パス。入力JSONには`provenance.phase="validation"`と`"snapshot_id"`・`"split_id"`が必要。任意の`experiment.snapshot_id` / `split_id`も入力と照合する。実データは同じSnapshot・SplitからValidationとTestのJSONを準備する。
- Test入力には`phase="test"`、`training_periods=["train","validation"]`、同じ出典が必要。`train_interactions`にはTrainとValidationの観測履歴を実際に含め、要求の履歴も更新する。Runnerは出典・元Train・Validationの正解ペアを含むことを検証するが、元イベントの時刻による再構成は行わない。未観測イベントまで補完しないこと。
- `search.max_trials`はseedを含めた全予定試行の上限。増やして`search`を再実行すれば既存成功を再利用し、続きへ進める。`time_budget_seconds`は1回のCLI呼出しの実時間の上限目安で、試行開始前に確認する。実行中の1試行を強制終了しない。
- `study.history`には各呼出しの範囲・予算を残す。探索条件を更新したら再選択・03での再確認が必要。データ量・Snapshot・コード・主指標を変えた場合は新しい`experiment.output`を指定する。
- `tracking.enabled=true`と`tracking_uri`・`experiment_name`を既存Benchmarkの`[tracking]`形式で指定すると、Smoke・Validation・Testの各試行をMLflowに記録する。`enabled=false`でもローカル成果物は保存する。
- 02のモデル間比較対象は`[comparison] configs=["another-study.toml"]`に追加する。同じTestファイル・評価プロトコル・コード・特徴出典だけを比較し、不一致時は比較を止める。

### Stage A / B / Cの指定

次は実装済みItemCFの設定例。`model.name="itemcf"`を使う。Popularityにこのgridを指定すると未対応パラメータとして失敗する。

```toml
[search]
stage = "A"
seeds = [0, 42]
max_trials = 8
time_budget_seconds = 1800
smoke_users = 3
smoke_train_users = 50
[search.grid]
neighbors = [20, 50, 100, 200]
```

Stage Bでは`stage="B"`とし、固定パラメータから`similarity`を外してgridへ`similarity=["cosine","jaccard"]`を追加する。Stage Cは新しい出力先で`stage="C"`・`parent="../stage-b/study.json"`を指定し、同じパラメータの数値範囲を絞る（例：`neighbors=[50,75,100]`）。カテゴリは親の候補から選ぶ。主指標とデータ条件を変えない。Stage Dに相当する操作が`select`であり、未指定の主指標から最良を決めることはない。

### 特徴・音源・未測定値

`experiment.features`はJSONへのパスで、形式は`{"dataset_id":"...","snapshot_id":"...","features":{"track_id":{"rock":0.8,"pop":0.2}}}`。非負・有限の実測特徴だけを指定し、ILDと03の特徴比較に使用する。特徴欠損のある要求はILD対象外とし、評価数も保存する。`[inspection.audio_files]`は`track_id="/absolute/path/to/audio.wav"`というローカル音源の対応表。

95%信頼区間はユーザー単位bootstrap、seed間標準偏差は実行間のばらつきとして別に表示する。既存Binary入力にはGradedに必要な観測がないため未測定とする。listening-v2では明示したgain条件ごとにGradedを表示する。Peak Memory・Tokens・API Cost・LLM Calls・Tool Callsは、モデルアダプタが返した実測値だけを集計し、未取得はnullとする。LLM/Agent自体や課金API呼出しは追加していない。

## 開発時の確認

```bash
uv run --locked --all-groups python -m pytest -q
uv run --locked --group dev ruff check src tests notebooks migrations
uv run --locked --group dev ruff format --check src tests notebooks migrations
uv build
```

2026-10-14の確認：213 passed / 2 skipped。既存Popularity・Benchmark・Notebook、選択設定からの02/03 fresh kernel、固定後Testの02表示、実MLflow SQLiteで成功・失敗・保存障害、1D/2D/3D探索、予算・再開・保存物不整合を確認した。CLIも合成データでSmoke→探索→選択→確認→固定→Test→成功結果再利用まで通した。1D線・2Dヒートマップ・トレードオフの図を実際に描画して確認した。実DBと大規模データの任意テストは未実行。
