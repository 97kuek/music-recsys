# 再生観測に基づく評価 v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution in this session, or superpowers:subagent-driven-development if the user selects delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 承認済みの評価仕様を実装し、観測事実のGroundTruthと、同じランキングからの3観点・期間・K・gain別評価をCLIで保存できるようにする。

**Architecture:** v1の既定動作と保存済み入力を維持し、`listening-v2`を明示選択する。候補判定だけを推薦モデルとEvaluatorで共用し、v2のParquet生成・Loader・指標・Runnerを責務ごとに分ける。GroundTruthにはgainを保存せず、推薦を各ユーザーのnew / repeatで一度ずつ生成してから条件別に採点する。

**Tech Stack:** Python 3.12、DuckDB、PyArrow、既存のpytest・MLflow・uv。依存追加なし。

**Spec:** [評価仕様 v2](../../evaluation-v2-spec.md)（ユーザー確認済み、2026-10-14）。分析結果は[GroundTruth調査](../../../experiments/music4all/ground-truth.md)。

## Global Constraints

- 主評価期間90日、期間感度30日・180日。開始以上・終了未満。
- ValidationはTrainで学習、TestはTrain＋Validation全期間で学習。元の境界は変更しない。
- 主評価K=10、補助K=5・20、感度K=2〜20。推薦要求は上位20曲。
- gainは`log2(1+n) + λ × log2(1+d)`、主条件λ=1。λ=0・0.5・2も保存ランキングから計算できる。
- `new`候補は学習カタログから本人の全既聴曲を除いた集合。`repeat`候補は学習カタログ内の本人の既聴曲。
- timezone-naiveを保持し、UTC・JSTを割り当てない。元IDの先頭ゼロ・大文字小文字を維持する。
- DuckDBの2GB・2スレッドを既定にする。全量をPythonの辞書や集合へ読み込まない。
- Runner上限は学習ユーザー2,000人・学習イベント500万件。読み込み前に実データから件数を確認する。
- GroundTruthの評価観測を推薦入力へ渡さない。未来の活動でユーザー・カタログ・履歴を選ばない。
- v2の不正ID・重複はその順位で0点とし、後続曲を繰り上げない。v1の正規化ルールを変えない。
- DB migration・テーブル変更、既存snapshot / splitの変更、実データでのモデル性能測定、Notebook UI改修は行わない。
- コミット・pushは依頼された場合だけ。計画中の各タスク後にも自動コミットしない。

## Review Focus

- 新規候補が空でもユーザーを未来の正解有無で削らない。Task 1・4・5で空候補を検証する。
- manifestが小さい件数を偽っても、大きなParquetをPythonへ展開しない。Task 2で実カウントとhashを検証する。
- 30日・90日の境界時刻ちょうどの再生を前の窓へ含めない。Task 3で日内重複と境界を同じfixtureで検証する。
- 無効・重複による空いた順位を詰めず、返却外の高gain正解をIDCGへ入れる。Task 4で独立した手計算を使う。
- λ・窓・Kを増やしても推薦呼出数や総時間を重複加算せず、生IDをMLflowへ送らない。Task 5で実RunnerとSQLite MLflowを検証する。

## File Structure

| ファイル | 責務 |
| --- | --- |
| `src/music_recsys/types.py` | 既定newを持つ推薦要求。既存のv1型は引き続き使用する |
| `src/music_recsys/candidates.py` | new / repeatの候補判定 |
| `src/music_recsys/listening_types.py` | v2の設定・観測・データセット・得点・結果の型と値検証 |
| `src/music_recsys/listening_data.py` | manifest / Parquetの検証、規模制限、要求の作成、ユーザー別の観測読込み |
| `src/music_recsys/listening_export.py` | 時系列分割から全量対応のv2 bundle生成 |
| `src/music_recsys/evaluation/listening_metrics.py` | gainと位置保持型の指標計算 |
| `src/music_recsys/evaluation/listening_benchmark.py` | 推薦の一度だけの実行、条件別集計・保存・Tracking |
| `src/music_recsys/evaluation/benchmark.py` | 既存CLI・設定からの明示的なv2分岐 |
| `src/music_recsys/temporal_cli.py` | 保存済みsplitからのv2 export分岐 |
| `tests/listening_support.py` | 合成Parquetとmanifestを作るテスト用fixture。実装側へテスト用処理を入れない |

既存の`recommenders/popularity.py`、`evaluation/metrics.py`は候補判定の共用箇所だけ変更する。既存の結果Readerにはv2を誤読しないための明示的な形式検証を追加する。

## Task 1: new / repeatの候補を共通化する

**Files:**
- Create: `src/music_recsys/candidates.py`, `tests/test_candidates.py`
- Modify: `src/music_recsys/types.py`, `src/music_recsys/recommenders/popularity.py`, `src/music_recsys/evaluation/metrics.py`
- Regression: `tests/test_data_and_popularity.py`, `tests/test_metrics.py`

**Interfaces:**
- `RecommendationRequest`の末尾に`task: Literal['new', 'repeat'] = 'new'`を追加する。既存の位置引数を変えない。
- `eligible_candidates(request: RecommendationRequest) -> tuple[str, ...]`をモデル・Evaluatorで共用する。

- [ ] **Step 1: 実モデルを使う失敗テストを書く。**

```python
def test_repeat_recommendation_keeps_previously_heard_items():
    from music_recsys.data import Interaction
    from music_recsys.recommenders.popularity import PopularityRecommender
    from music_recsys.types import RecommendationRequest

    model = PopularityRecommender((Interaction('u', 'a'), Interaction('v', 'b')))
    request = RecommendationRequest('r', 'u', ('a',), ('a', 'b'), 20, task='repeat')
    assert model.recommend(request).item_ids == ('a',)
```

既定newでは同じ入力から`b`だけが返ること、空候補、未知のtask拒否も追加する。既存の「履歴が候補にあっても除外する」テストを残す。

- [ ] **Step 2: REDを確認する。** `.venv/bin/python -m pytest -q tests/test_candidates.py`。未対応taskを原因に失敗することを確認する。
- [ ] **Step 3: 型検証と候補判定を実装する。** 中核は以下とし、入力順を保持する。

```python
def eligible_candidates(request):
    history = set(request.history)
    return tuple(
        item for item in request.candidate_ids
        if (item in history) == (request.task == 'repeat')
    )
```

Popularityの`.difference(request.history)`と既存Evaluatorの候補作成をこの関数へ置き換える。v1 Loaderはtaskを指定せず、従来どおりnewとする。

- [ ] **Step 4: GREENと既存動作を確認する。** `.venv/bin/python -m pytest -q tests/test_candidates.py tests/test_data_and_popularity.py tests/test_metrics.py tests/test_benchmark.py`。

## Task 2: v2の型・bundle Loaderを作る

**Files:**
- Create: `src/music_recsys/listening_types.py`, `src/music_recsys/listening_data.py`, `tests/listening_support.py`, `tests/test_listening_data.py`

**Interfaces:**
- `ListeningConfig(window_days=(30,90,180), primary_window_days=90, ks=tuple(range(2,21)), primary_k=10, day_weights=(1.0,), primary_day_weight=1.0)`。非空・重複なし・範囲・主条件の包含を検証する。
- `PlaybackObservation(user_id, item_id, window_days, learning_play_count, learning_active_days, evaluation_play_count, evaluation_active_days)`。dataclassはfrozen。
- `ListeningDataset`は`dataset_id`, `catalog_ids`, `train_interactions`, `requests`, `observations_path`, `manifest`, `sha256`を持つ。future観測をrequestsへ埋め込まない。
- `load_listening_dataset(bundle: Path, config: ListeningConfig) -> ListeningDataset`。
- `read_user_observations(connection, user_id: str) -> tuple[PlaybackObservation, ...]`。Runnerが検証済みParquet viewを登録したDuckDB connectionを使う。全ユーザーの観測を一括Python化しない。

- [ ] **Step 1: 手書きのParquet fixtureとLoaderテストを書く。** fixtureは以下の内容をPyArrowで書き、実SHA-256とサイズをmanifestへ記録する。manifestの計算には`ingestion.types.canonical_json`を使う。

```python
learning = [
    {'user_id': 'u', 'item_id': 'a', 'play_count': 2, 'active_days': 1},
    {'user_id': 'v', 'item_id': 'b', 'play_count': 1, 'active_days': 1},
]
observations = [{
    'user_id': 'u', 'item_id': 'b', 'window_days': 90,
    'learning_play_count': 0, 'learning_active_days': 0,
    'evaluation_play_count': 3, 'evaluation_active_days': 2,
}]
```

```python
def test_loader_keeps_truth_out_of_requests(tmp_path):
    from listening_support import write_bundle
    from music_recsys.listening_data import load_listening_dataset
    from music_recsys.listening_types import ListeningConfig

    bundle = write_bundle(tmp_path)
    config = ListeningConfig(window_days=(90,), ks=(2, 10, 20))
    data = load_listening_dataset(bundle, config)
    requests = {(r.user_id, r.task): r for r in data.requests}
    assert requests['u', 'new'].candidate_ids == ('b',)
    assert requests['u', 'repeat'].candidate_ids == ('a',)
    assert len(data.train_interactions) == 3
    assert not hasattr(requests['u', 'new'], 'observations')
```

同じテストモジュールで、ファイル改変、絶対・親移動パス、重複キー、coldな観測、学習回数不一致、bool / 負数 / 非有限λ、不正な日数を拒否する。manifestがユーザー数を小さく偽るケースは実Parquetに2,001ユーザーを書いて拒否を確認する。

- [ ] **Step 2: REDを確認する。** `.venv/bin/python -m pytest -q tests/test_listening_data.py`。
- [ ] **Step 3: Loaderを実装する。** manifest版とprotocol、相対パス、実サイズ・hash、PyArrow列型、DuckDBによる一意性・整合性の順に検証する。全実ファイルの検証後、`learning`から`count(DISTINCT user_id)`と`sum(play_count)`を計算して上限を適用する。集計結果から履歴とカタログを作り、ユーザーID順・new/repeat順の要求を作る。

学習イベントを既存モデルへ渡す復元処理は上限確認後だけにする。

```python
interactions = tuple(
    Interaction(user_id, item_id)
    for user_id, item_id, play_count in learning_rows
    for _ in range(play_count)
)
```

全future観測はPython化せず、検証済みファイルのパスを保持する。観測読込みはパラメータ化した`WHERE user_id = ? ORDER BY item_id,window_days`で要求ユーザー分だけ取得する。observationsの空ファイルも正しい列型で許可する。

- [ ] **Step 4: GREENを確認する。** `.venv/bin/python -m pytest -q tests/test_listening_data.py tests/test_candidates.py`。

## Task 3: 保存済み分割から全量対応のGroundTruthを生成する

**Files:**
- Create: `src/music_recsys/listening_export.py`, `tests/test_listening_export.py`, `tests/test_listening_scale.py`
- Modify: `src/music_recsys/temporal_cli.py`, `tests/db/test_temporal_pipeline.py`

**Interfaces:**
- `export_listening_phase(files: dict[str, Path], output: Path, temporal: TemporalConfig, *, phase: str, windows: tuple[int, ...]=(30,90,180), provenance: dict, memory_limit: str='2GB') -> Path`。返却値は完成したmanifestのPath。
- `export_split(..., phase: str, protocol: str='binary-v1', windows: tuple[int,...]=(30,90,180))`へ後方互換のkeywordを追加する。
- CLIの`export`へ`--protocol listening-v2`と任意の`--window-days`を追加する。既定binary-v1の出力は既存JSONのまま。

- [ ] **Step 1: 境界を含むイベントfixtureで失敗テストを書く。** `tests/listening_support.py`の`write_partitions`は以下の行をTrain／Validation／Testに分け、既存の3列スキーマでParquetへ書く。DuckDBの出力を期待値には使わず、下記の数値を手書きで照合する。

```python
rows = [
    ('u','a','2017-03-28T09:00:00'), ('v','b','2017-03-28T09:00:00'),
    ('u','b','2017-03-29T00:00:00'), ('u','b','2017-03-29T01:00:00'),
    ('u','b','2017-03-30T00:00:00'), ('u','a','2017-04-28T00:00:00'),
    ('u','b','2017-06-27T00:00:00'), ('cold','a','2017-04-01T00:00:00'),
    ('u','cold_item','2017-04-01T00:00:00'), ('u','b','2018-09-21T00:00:00'),
]
```

```python
def test_export_counts_dates_and_excludes_window_end(tmp_path):
    import pyarrow.parquet as pq
    from listening_support import write_partitions
    from music_recsys.listening_export import export_listening_phase
    from music_recsys.temporal import TemporalConfig

    files = write_partitions(tmp_path / 'source')
    out = tmp_path / 'bundle'
    export_listening_phase(files, out, TemporalConfig(), phase='validation', provenance={})
    actual = {
        (r['user_id'], r['item_id'], r['window_days']):
        (r['evaluation_play_count'], r['evaluation_active_days'])
        for r in pq.read_table(out / 'observations.parquet').to_pylist()
    }
    assert actual == {
        ('u','b',30): (3,2), ('u','b',90): (3,2), ('u','b',180): (4,3),
        ('u','a',90): (1,1), ('u','a',180): (1,1),
    }
```

TestのTrain＋Validation学習、future変更による過去要求の不変性、空の観測、再実行の内容hash一致、既存出力拒否、途中失敗でmanifestを作らないことも追加する。

- [ ] **Step 2: REDを確認する。** `.venv/bin/python -m pytest -q tests/test_listening_export.py`。
- [ ] **Step 3: 2段階の分割集計と読戻し検証を実装する。** 対象期間だけを読み、ユーザーhashの32分割をディスクへ書く。先に全分割の学習集計を完成させ、全学習カタログを確定する。その後に評価分割を集計してcold判定する。各評価分割で一度ユーザー×曲×日へまとめ、窓別の回数を`sum(day_plays)`、日数を行数で数える。

```sql
SELECT user_id,item_id,CAST(event_time AS DATE) AS day,count(*) AS day_plays
FROM phase_events
WHERE event_time >= $prediction_at AND event_time < $max_window_end
GROUP BY user_id,item_id,day
```

```sql
SELECT user_id,item_id,sum(day_plays)::BIGINT AS evaluation_play_count,
       count(*)::BIGINT AS evaluation_active_days
FROM evaluation_daily
WHERE day < $window_end
GROUP BY user_id,item_id
```

日単位の比較を使うのは仕様の推薦時点が日境界の場合だけ。カスタムTemporalConfigが日境界でなければ、元のevent_timeで各窓の範囲を切ってから日付を数え、開始日の前後を混ぜない。COPYやParquetパスと境界には名前付きパラメータを使い、`?`の束縛順の混同を防ぐ。

各分割を並べ替え、最後に全体を外部ソートして固定writer設定で出力する。原集計と読戻しの行をArrow batchで厳密照合し、一意性・所属・回数合計・日数を確認してからmanifestを最後に保存する。出典の内容hash・schema・境界・windows・処理版をmanifestへ記録し、時刻・絶対パスを内容hashから除く。

- [ ] **Step 4: CLI分岐とGREENを確認する。** `.venv/bin/python -m pytest -q tests/test_listening_export.py tests/test_temporal.py`。DB経由は既存の隔離schemaテストへv2 export→Loaderの往復を追加し、有効化時に実行する。
- [ ] **Step 5: 小さいメモリで集計を確認する。** `tests/test_listening_scale.py`で複数分割にまたがる長いIDの合成イベントを作り、`memory_limit='128MB'`で回数・日数・cold分類の一致を確認する。巨大データの回帰ケースは既存の`MUSIC_RECSYS_TEST_SCALE=1`へ合わせる。検証用の設定をmonkeypatchで製品コードへ隠し込まない。

## Task 4: gainと位置保持型Evaluatorを実装する

**Files:**
- Create: `src/music_recsys/evaluation/listening_metrics.py`, `tests/test_listening_metrics.py`
- Modify: `src/music_recsys/listening_types.py`（`ListeningScore`）

**Interfaces:**
- `graded_gain(play_count: int, active_days: int, day_weight: float) -> float`。
- `evaluate_listening_response(request, response, observations: tuple[PlaybackObservation,...], *, window_days: int, k: int, day_weight: float) -> ListeningScore`。
- `ListeningScore`は`binary_ndcg`, `graded_ndcg`, `recall`, `relevant_count`, `returned_count`, `valid_count`, `invalid_count`, `duplicate_count`, `missing_count`, `overflow_count`を持つ。
- `returned_count`は先頭K内の返却位置数、`valid_count`は有効なユニーク曲数、`missing_count`は未返却位置数。無効・重複による穴は別に数え、詰めない。

- [ ] **Step 1: 返却外の最高gainと重複を含む手計算テストを書く。**

```python
def test_graded_idcg_uses_all_truth_and_duplicate_does_not_shift_positions():
    import math
    import pytest
    from music_recsys.types import RecommendationRequest, RecommendationResponse
    from music_recsys.listening_types import PlaybackObservation
    from music_recsys.evaluation.listening_metrics import evaluate_listening_response

    request = RecommendationRequest('r','u',(),('a','b','c'),20)
    response = RecommendationResponse('r',('a','a','outside','b'))
    observations = (
        PlaybackObservation('u','a',90,0,0,1,1),
        PlaybackObservation('u','b',90,0,0,3,2),
        PlaybackObservation('u','c',90,0,0,9,3),
    )
    score = evaluate_listening_response(
        request,response,observations,window_days=90,k=2,day_weight=1.0)
    ideal = (math.log2(10)+2) + (2+math.log2(3))/math.log2(3)
    assert score.graded_ndcg == pytest.approx(2/ideal)
    assert score.recall == pytest.approx(1/3)
```

λ=0、回数のみ／日数違い、repeatの既聴正解、空正解→null、正解あり失敗→0、候補外・短い出力・20位超過・要求ID不一致も追加する。

- [ ] **Step 2: REDを確認する。** `.venv/bin/python -m pytest -q tests/test_listening_metrics.py`。
- [ ] **Step 3: gainとDCGを実装する。** gainの中核は次の式。件数・λの値検証を先に行う。

```python
gain = math.log2(1 + play_count) + day_weight * math.log2(1 + active_days)
```

先頭20の各位置を走査し、有効な最初の出現だけにgainを割り当てる。全正解のgainをソートしてIDCGの先頭Kを取り、返却に含まれない正解も残す。K・窓・λごとにランキングを正規化し直して詰めない。future観測のユーザー不一致や学習／候補矛盾は入力エラーとして拒否する。

- [ ] **Step 4: GREENと旧指標を確認する。** `.venv/bin/python -m pytest -q tests/test_listening_metrics.py tests/test_metrics.py tests/test_candidates.py`。

## Task 5: 一度だけ生成するRunner、CLI、Trackingと結果保存

**Files:**
- Create: `src/music_recsys/evaluation/listening_benchmark.py`, `tests/test_listening_benchmark.py`, `tests/test_listening_tracking.py`, `configs/music4all-listening-validation.toml`
- Modify: `src/music_recsys/evaluation/benchmark.py`, `src/music_recsys/experiment_analysis.py`, `tests/test_experiment_analysis.py`, `docs/evaluation-protocol.md`, `docs/music4all-benchmark.md`, `docs/result-analysis.md`, `README.md`

**Interfaces:**
- `run_listening_benchmark(dataset: ListeningDataset, recommend, *, config: ListeningConfig, model_name: str, seed: int=0, training_seconds: float=0.0, config_sha256: str|None=None, model_parameters: dict|None=None, tracking: TrackingConfig|None=None) -> ListeningReport`。
- `save_listening_report(report: ListeningReport, output_root: Path) -> Path`。
- `ListeningReport`は`schema_version=2`, `metadata`, `summary`（条件別の集計行）, `runs`（生成要求・応答・一度だけの時間・その要求の得点）, `tracking`を持つ。
- `ListeningMetric`の条件は`task`, `window_days`, `k`, `day_weight`, `metric`。要求別は`value`と正解数、集計行は`value`, `n_requests`, `n_evaluated`, `n_excluded`, `n_failed`を持つ。
- 旧`load_config`へ既定`protocol='binary-v1'`、任意`listening_config`を追加し、`run_experiment`・保存・表示を明示分岐する。新しいmoduleのimportはv2選択時に行い、v1だけならdata依存を必須にしない。

- [ ] **Step 1: 実Runnerの呼出数と得点の失敗テストを書く。** mockは使わず、小さな決定的な推薦関数から返したランキング・保存指標を確認する。

```python
def test_runner_reuses_two_rankings_for_all_conditions(tmp_path):
    from listening_support import write_bundle
    from music_recsys.listening_types import ListeningConfig
    from music_recsys.listening_data import load_listening_dataset
    from music_recsys.types import RecommendationResponse
    from music_recsys.evaluation.listening_benchmark import run_listening_benchmark

    config = ListeningConfig(window_days=(90,), day_weights=(0.0,0.5,1.0,2.0))
    data = load_listening_dataset(write_bundle(tmp_path), config)
    calls = []
    def recommend(request):
        calls.append((request.user_id,request.task))
        return RecommendationResponse(request.request_id,request.candidate_ids[:20])
    report = run_listening_benchmark(data,recommend,config=config,model_name='fixed')
    assert sorted(calls) == [('u','new'),('u','repeat'),('v','new'),('v','repeat')]
    assert len(report.runs) == 4
    assert {r.k for r in report.summary} == set(range(2,21))
```

空候補では推薦を呼ばないこと、正解なしでも候補があれば生成すること、推薦失敗とEvaluator内部エラーを混同しないこと、保存→JSON/CSV読戻しを追加する。v2を旧結果Readerへ渡すと明示的ValueErrorになることも確認する。

- [ ] **Step 2: REDを確認する。** `.venv/bin/python -m pytest -q tests/test_listening_benchmark.py tests/test_listening_tracking.py`。
- [ ] **Step 3: Runnerと設定・CLIを実装する。** 学習は1回。要求ごとに一度だけ生成し、検証済み観測をユーザー分だけ読み、窓・K・λの指標を得る。binary指標はλに依存しないので重複行を作らない。精度は条件ごとのユーザー平均、失敗も正解があれば0、分母0はnull。モデル例外・不正Responseだけを要求失敗に変換し、Loaderや得点実装のバグまでモデル失敗として隠さない。

新設定例は以下とする。datasetはv2 bundleのディレクトリ。

```toml
[benchmark]
protocol = "listening-v2"
dataset = "../data/benchmarks/music4all-listening/validation"
model = "popularity"
top_k = 20
seed = 42

[evaluation]
window_days = [30, 90, 180]
primary_window_days = 90
primary_k = 10
day_weights = [0.0, 0.5, 1.0, 2.0]
primary_day_weight = 1.0
```

主条件だけで使う場合は`day_weights=[1.0]`にする。無効なprotocol、v1入力とv2設定の混在、top_kが20以外のv2設定は拒否する。

- [ ] **Step 4: 結果・MLflowを実装し、実SQLite backendで検証する。** 既存の`ExperimentRun`を使用し、条件と条件別集計をlogする。要求の生ID・履歴・観測はローカルの`results.json`だけへ保存し、MLflowへ送るのは集計版JSON・CSV・summaryと詳細参照のmanifestだけ。詳細参照は相対パス・SHA-256・run IDを記録する。生成時間を各条件へ合算せず、`n_generation_calls`と要求ごとの時間から総計する。推薦失敗があればrunをFAILEDとして残す。
- [ ] **Step 5: CLIと文書を仕上げる。** v2 exportとBenchmarkの明示選択、同じnewランキングの共用、CSV条件列、旧Readerとの区別を文書化する。実データの性能値は作らない。更新済み仕様・調査の過去記録を書き換えて実行済みと誤認させない。
- [ ] **Step 6: GREENと全体確認。** 以下を実行し、終了コードと件数を確認する。

```bash
.venv/bin/python -m pytest -q tests/test_listening_data.py tests/test_listening_export.py tests/test_listening_metrics.py tests/test_listening_benchmark.py tests/test_listening_tracking.py
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests notebooks migrations
.venv/bin/ruff format --check src tests notebooks migrations
git diff --check
uv build
```

Notebookのローカルポート制限で失敗する場合は必要なsandbox承認を得て同じ全テストを再実行する。DB往復テストは隔離schemaと既存の明示的有効化を使い、通常DBの実験行を書き換えない。ビルド用依存が未取得なら通常の承認経路で取得し、依存設定を変えて回避しない。

## 実行前の確認と最終レビュー

- 現在の`docs/ground-truth-design`ブランチには、今回の調査成果物と承認済み仕様が未コミットである。実装前から存在するこれらを削除・置換しない。新たなworktreeの必要性は作業開始時に確認する。
- 設計前の基準は182 passed / 2 skipped。skipは既存のopt-inテストであり、新機能の検証成功を意味しない。
- 各TaskはRED→実装→GREENを順に進める。レビューのためだけの自動コミットはしない。
- 最後に仕様との照合と全差分レビューを行い、未実施の実データ性能評価・Notebook表示・多様性・追加コスト計測を実装済みと報告しない。

## 推奨する実行方法

このセッションで順に実装する**Native実行**を推奨する。型・保存形式・Evaluator・Runnerの依存が強く、各段階のテストを引き継いで進める方が変更の整合性を保ちやすい。計画のユーザーレビュー後に実装へ進む。
