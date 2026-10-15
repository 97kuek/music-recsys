# 従来モデル一式と評価の残課題 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 従来7モデルを共通の探索・分析基盤へ接続し、v2多様性指標と実データValidation検証を完成させる。

**Architecture:** 既存のモデルfactoryとRequest / Responseを維持し、特徴artifactの読込みとモデルへの受渡しを共通化する。各アルゴリズム・v2多様性集計を独立したsrcモジュールに置き、CLIとNotebookは共通処理へ委譲する。旧成果物を上書きせず、新しい実験条件として保存する。

**Tech Stack:** Python 3.12、uv、NumPy（因子モデルのみ遅延import）、DuckDB / PyArrow、pytest、MLflow、既存Jupyter / matplotlib。

**Spec:** `docs/superpowers/specs/2026-10-15-model-suite-design.md`

## Global Constraints

- LLM推薦・Agent推薦はユーザー指示で保留し、今回の実装・API接続・実験に含めない。
- 既存のPopularity・ItemCF、v1の保存物、listening-v2、CLI、MLflow、01→02→03の導線を維持する。
- 全モデルが共通候補判定に従い、new / repeatの両方を処理する。
- ILDはK>=2。有効曲2曲未満、欠損・ゼロノルムの特徴ではnull。
- Testの正解を使ったパラメータ探索は禁止する。実データの主指標未指定時は診断実験のみ。
- 合成データの性能を実データの結果や最適値として報告しない。
- 元データ・DBの通常schema・既存実験履歴を書き換えない。DB検証は一時schemaを使う。
- コード・設定・文書・出力を日本語のコミットメッセージで論理単位に保存し、前回の依頼に従いpushする。

## Review Focus

1. 手作業で改変した特徴artifactや別Snapshotの特徴を、単発CLI・探索・03のいずれからも使えないこと（Task 1 / 6）。
2. 先頭K内の不正・重複位置を除去した結果、Kより後の曲が多様性へ繰り上がらないこと（Task 2）。
3. 全曲既聴・ほぼ全曲既聴のBPRユーザーで負例生成が停止し、未学習因子を嗜好の根拠として出さないこと（Task 4）。
4. 履歴・候補・特徴の順番とseed、極端な数値・メモリ要求が、再現性や正常終了の誤認を招かないこと（Task 3 / 4 / 5）。
5. 旧v2の追加指標欠損・主指標未指定・比較条件不一致を、0や最適設定確定として表示しないこと（Task 2 / 6 / 7）。

## ファイルと責務

| ファイル | 責務 |
| --- | --- |
| `src/music_recsys/features.py` | 出典付き特徴JSONの読込み・型検証・Snapshotタグのexport |
| `src/music_recsys/evaluation/listening_diversity.py` | 保存順位の先頭位置を維持したv2 Beyond-Accuracy |
| `src/music_recsys/recommenders/usercf.py` | 二値UserCF |
| `src/music_recsys/recommenders/factors.py` | 因子モデル用の学習index・候補スコア・説明・メモリ検証 |
| `src/music_recsys/recommenders/als.py` | implicit ALS |
| `src/music_recsys/recommenders/bpr.py` | BPR-MFと有限時間の負例抽出 |
| `src/music_recsys/recommenders/content.py` | タグ変換・ユーザー嗜好・Cosine推薦 |
| `src/music_recsys/recommenders/hybrid.py` | 重み付きRRF |
| 既存factory / Benchmark / Search / Inspection | 特徴受渡し・モデル登録・保存・既存互換性 |
| 既存listening_analysis / search_analysis / Notebook | 保存済み値からの表示・比較・診断結果の読込み |
| `configs/workflow-*.toml`、`docs/models.md` | モデル別の同条件探索例、定義・手順 |

## 共通インターフェース

```python
FeatureMap = dict[str, dict[str, float]]

def create_recommender(model, interactions, *, parameters=None, seed=0, features=None): ...
def load_features(path, *, dataset_id, snapshot_id) -> FeatureMap: ...
def export_snapshot_features(engine, storage_root, snapshot_id, output) -> dict: ...
def inspect_recommendation(dataset, *, model, request_id, parameters=None, seed=0, features=None): ...
```

`features`は任意の追加keyword。既存の呼出しを壊さない。テスト用に差し替えた既存factoryへ新keywordを強制しないため、`FEATURE_MODELS=("content","hybrid")`だけに特徴を渡す。評価側はモデルに関係なく同じ特徴artifactを使う。

## Task 1: 出典付き特徴とモデル入力の共通化

**Files:** Create `src/music_recsys/features.py`; Modify `experiment_search.py`, `evaluation/benchmark.py`, `recommendation_inspection.py`, `recommenders/__init__.py`; Test `tests/test_features.py`, `tests/db/test_features.py`。

**Interfaces:** Consumes: 保存済みSnapshotと既存`_features`のJSON形式。Produces: 上記`load_features`、`export_snapshot_features`、factory / inspectionの後方互換keyword。

- [ ] Step 1: 型・出典・hash・欠損を検証する失敗テストを書く。

```python
def test_feature_artifact_is_bound_to_snapshot(tmp_path):
    import json
    import pytest
    from music_recsys.features import load_features
    path = tmp_path / "features.json"
    path.write_text(json.dumps({"dataset_id": "toy", "snapshot_id": "s1",
                                "features": {"a": {"rock": 1.0}}}))
    assert load_features(path, dataset_id="toy", snapshot_id="s1")["a"] == {"rock": 1.0}
    with pytest.raises(ValueError):
        load_features(path, dataset_id="toy", snapshot_id="s2")
```

旧形式に任意の追加出典を要求せず、追加したexport形式では元メタデータhash・変換版を保存する。bool / NaN / inf / 負値、非文字列ID・特徴名、ゼロベクトル、部分欠損を追加テストで区別する。

- [ ] Step 2: `.venv/bin/python -m pytest -q tests/test_features.py`。Expected: import / 未実装エラーでRED。
- [ ] Step 3: 既存`_features`の検証を共通関数へ移し、Snapshot exportを実装する。

```python
# exportの変換規則：保存済みメタデータのみ。利用可能なタグがない曲は省略する。
features = {track.source_id: track.metadata["tags"] for track in catalog.tracks
            if track.metadata and track.metadata.get("tags")}
# JSONにはdataset_id、snapshot_id、元Snapshot/メタデータhash、変換版とfeaturesを保存。
```

CLIには`python -m music_recsys.features --snapshot-id UUID --output PATH`を設け、既存出力を拒否する。秘密情報を表示しない。Benchmarkの追加設定は`benchmark.features`、探索は既存`experiment.features`を利用する。

- [ ] Step 4: 単体と既存factory / Benchmark / Inspectionを実行する。Expected: GREEN。隔離DBテストでexport前後の通常DB件数一致・別Snapshot拒否・元payload保持を確認する。
- [ ] Step 5: `feat: 出典付きタグ特徴をモデルと評価で共有`としてコミット。

## Task 2: v2多様性の採点・保存・分析入力

**Files:** Create `evaluation/listening_diversity.py`; Modify `evaluation/listening_benchmark.py`, `search_protocols.py`, `listening_analysis.py`; Test `tests/test_listening_diversity.py`, `tests/test_listening_benchmark.py`, `tests/test_listening_workflow_analysis.py`。

**Interfaces:** Consumes: `ListeningDataset`、保存されたrequest / response、FeatureMap。Produces: `listening_diversity(dataset, runs, *, ks, features=None) -> dict`。戻り値は`metrics`（task / k / ild / novelty / catalog_coverage / 評価人数）と`requests`（要求別値）、`definitions`。窓やgainとは別の`beyond_accuracy`としてreport / curvesへ任意追加する。

- [ ] Step 1: 小さな履歴で手計算したテストを書く。

```python
def test_invalid_positions_never_pull_later_tracks_into_top_k(diversity_fixture):
    from music_recsys.evaluation.listening_diversity import listening_diversity
    data, runs, features = diversity_fixture(["invalid", "a", "a", "b"])
    result = listening_diversity(data, runs, ks=(1, 2, 3, 4), features=features)
    rows = {r["k"]: r for r in result["metrics"] if r["task"] == "new"}
    assert rows[1]["novelty"] is None
    assert rows[2]["ild"] is None
    assert rows[3]["ild"] is None
    assert rows[4]["ild"] == 1.0  # a,bは直交する特徴
```

fixtureは2学習ユーザー、a,bは別の本人未聴曲、候補はa,b、特徴a={rock:1}, b={jazz:1}、返却は上記順とする。Noveltyは学習ユニークユーザーに基づく手計算、Coverageは候補和集合分母と照合する。repeat、空分母、生成失敗、特徴欠損、ゼロノルム、窓・λ増加で値とモデル呼出数が増えないケースも固定する。

- [ ] Step 2: `.venv/bin/python -m pytest -q tests/test_listening_diversity.py`。Expected: RED。
- [ ] Step 3: 先頭Kを切ってから有効・重複判定する。

```python
ranked, seen = [], set()
for item in response.item_ids[:k]:
    if item in eligible and item not in seen:
        seen.add(item)
        ranked.append(item)
```

全学習ユーザー数と曲のユニークユーザー数を1回集計し、期間の正解なしも集計母集団に残す。モデルへ追加情報を渡さず、保存ランキングだけで計算する。既存CSVのaccuracy行を変更せず、追加指標は別CSV / optional artifactへ保存する。MLflowは集計のみを記録する。

- [ ] Step 4: 上記テストとv2 Benchmark・Workflow・Trackingを実行。Expected: GREEN。追加キーのない旧v2を読むとnullになり、内容hashの再生成や元結果の上書きがない。
- [ ] Step 5: `feat: listening-v2の多様性と発見性を計測`としてコミット。

## Task 3: UserCF

**Files:** Create `recommenders/usercf.py`; Modify `recommenders/__init__.py`; Test `tests/test_usercf.py`。

**Interfaces:** `UserCFRecommender(interactions, *, neighbors=50, similarity="cosine")`、共通`recommend(request)`と`explain(request,item_id)`。説明は`score / reason / contributors / fallback`。

- [ ] Step 1: 手計算の近傍・自己除外・二値化を固定する。

```python
def test_usercf_scores_are_similarity_sums():
    import math
    import pytest
    from music_recsys.data import Interaction
    from music_recsys.recommenders import create_recommender
    from music_recsys.types import RecommendationRequest
    events = [Interaction(u, i) for u, items in
              [("u", "a"), ("v", "ab"), ("w", "acd")] for i in items]
    model = create_recommender("usercf", events, parameters={"neighbors": 2})
    r = RecommendationRequest("r", "u", ("a",), ("a", "b", "c", "d"), 20)
    assert model.recommend(r).item_ids == ("b", "c", "d")
    assert model.explain(r, "b")["score"] == pytest.approx(1 / math.sqrt(2))
```

Jaccard、repeat、空候補・未知user/item、同点、再生重複で類似度不変、neighbors bool/0/負値、未知設定もテストする。

- [ ] Step 2: `.venv/bin/python -m pytest -q tests/test_usercf.py`。Expected: 未登録でRED。
- [ ] Step 3: item→usersの疎な転置indexから本人以外の共聴ユーザーを数え、近傍上位を決定的に保存する。候補曲ごとに近傍を選び直さない。根拠0は学習Popularity・ID順で補完し、説明へ残す。
- [ ] Step 4: UserCFと既存ItemCF・Popularity・候補テストを実行。Expected: GREEN。
- [ ] Step 5: `feat: UserCF推薦モデルを追加`としてコミット。

## Task 4: ALSとBPR

**Files:** Create `recommenders/factors.py`, `recommenders/als.py`, `recommenders/bpr.py`; Modify `recommenders/__init__.py`, `pyproject.toml`, `uv.lock`; Test `tests/test_factor_models.py`。

**Interfaces:** `ALSRecommender(interactions, *, factors=32, iterations=10, alpha=10.0, regularization=0.1, seed=0, memory_budget_mb=512)`。`BPRRecommender(interactions, *, factors=32, iterations=20, learning_rate=0.05, regularization=0.01, seed=0, memory_budget_mb=512)`。`unseen_at_rank(observed_sorted, n_items, rank) -> int`。共通recommend / explainはTask 3と同じ。

- [ ] Step 1: ALSの正規方程式を小行列の直接解と比較し、BPRの1更新で正例－負例marginが増えることを独立した数値で固定する。ユーザー×曲の密行列を作らないメモリ予算拒否、極端なalpha / lr / reg、同seedとイベント順入替で同結果、全曲既聴で停止を確認する。

```python
def test_dense_user_negative_sampling_is_finite():
    from music_recsys.recommenders.bpr import unseen_at_rank
    assert unseen_at_rank(tuple(range(99999)), 100000, 0) == 99999
    assert unseen_at_rank((0, 2, 4), 6, 0) == 1
    assert unseen_at_rank((0, 2, 4), 6, 2) == 5
```

全曲既聴ユーザーはBPRの抽出対象外とし、学習不能なユーザー・cold user / itemの説明はPopularity補完とする。因子内積を音楽的理由にしない。

- [ ] Step 2: `.venv/bin/python -m pytest -q tests/test_factor_models.py`。Expected: RED。
- [ ] Step 3: NumPyをuvの`models` groupへ追加しlockを更新する。モデルimport時だけ読み、Popularity利用だけでは要求しない。学習IDをsortし、各ユーザーの曲別回数を圧縮したindexを使用する。

```python
# ALS: 各ユーザーの観測曲rows、confidence=1+alpha*count。
a = item_factors.T @ item_factors + regularization * np.eye(factors)
a += (rows.T * (confidence - 1)) @ rows
b = rows.T @ confidence
user_factors[u] = np.linalg.solve(a, b)
# 曲側の更新も観測ユーザーindexで対称に解く。
```

BPRは各iterationで学習のユニーク正例ペアをseed付き順序で扱い、本人の未聴学習カタログから一様に負例を選ぶ。補集合全体をユーザーごとに保存せず、未観測位置のrankを二分探索でIDへ変換する。更新前因子を使った安定sigmoid勾配、有限値検証、予算検証を設ける。

- [ ] Step 4: 因子テストとfactory / Benchmarkを実行。Expected: GREEN。推定メモリ予算超過は配列確保前に失敗し、学習失敗を正常runとして記録しない。
- [ ] Step 5: `feat: 暗黙ALSとBPR推薦モデルを追加`としてコミット。

## Task 5: Content-BasedとHybrid

**Files:** Create `recommenders/content.py`, `recommenders/hybrid.py`; Modify `recommenders/__init__.py`; Test `tests/test_content_hybrid.py`。

**Interfaces:** `ContentRecommender(interactions, *, features, use_idf=True, aggregation="binary")`。aggregationはbinary / log_count。`HybridRecommender(interactions, *, features, cf_model="itemcf", neighbors=50, similarity="cosine", cf_weight=0.5, rrf_constant=60, pool_size=100, use_idf=True, aggregation="binary")`。CFはitemcf / usercf、pool_sizeは20以上。

- [ ] Step 1: 同タグが上位、直交タグが0、欠損の補完、学習外曲を追加してもIDF不変、RRF端点と中間重みをテストする。

```python
def test_content_cosine_has_feature_evidence(content_fixture):
    import pytest
    from music_recsys.recommenders import create_recommender
    events, request, features = content_fixture
    model = create_recommender("content", events, features=features,
                               parameters={"use_idf": False})
    assert model.recommend(request).item_ids[0] == "b"
    assert model.explain(request, "b")["score"] == pytest.approx(0.8 / (0.8**2+0.2**2)**0.5)
```

fixtureは本人履歴a={rock:1}、b={rock:.8,pop:.2}、c={jazz:1}。他ユーザーにb,cを学習させ、候補b,cとする。bool / NaN / 範囲外重み・負RRF・不足pool、new / repeatも固定する。

- [ ] Step 2: `.venv/bin/python -m pytest -q tests/test_content_hybrid.py`。Expected: RED。
- [ ] Step 3: カタログ内のdocument frequencyで`smooth_idf=log((1+n)/(1+df))+1`を求め、曲ベクトルをL2正規化する。履歴を二値またはlog1p回数で集約し、Cosineを返す。Hybridは双方のtop poolから`weight/(rrf_constant+rank)`を合算する。
- [ ] Step 4: 全モデル単体・候補・Inspectionを実行。Expected: GREEN。cf_weight=1 / 0で各基底のtop20と一致し、説明は各モデルの順位・寄与を表示する。
- [ ] Step 5: `feat: タグ推薦とHybrid推薦モデルを追加`としてコミット。

## Task 6: CLI探索・Notebook・保存物の全モデル統合

**Files:** Modify `experiment_search.py`, `search_protocols.py`, `listening_analysis.py`, `search_analysis.py`, `notebooks/01_data.ipynb`, `notebooks/02_analysis.ipynb`, `notebooks/03_recommendation.ipynb`; Create model別toy TOML / 出典付きtoy特徴; Test `tests/test_model_suite_workflow.py`, `tests/test_model_suite_notebook.py`。

**Interfaces:** Task 1のfeatures keywordを全呼出し元へ接続。`trial_curves(config, trial_id, *, resamples=2000)`を追加し、主指標未指定の診断runを選択artifactなしで読めるようにする。`selected_curves`は同じReaderへ委譲する。診断の表示は最良選択・固定・Testの許可にはならない。

- [ ] Step 1: 7モデルをparametrizeし、同じv1 / v2合成分割で全工程・全seed・hash再利用・特徴不一致・コード変更拒否を実行するテストを書く。探索変数は各モデルの実在パラメータを使用し、Popularityに架空のgridを追加しない。

```python
@pytest.mark.parametrize("model", ["popularity", "itemcf", "usercf", "als", "bpr", "content", "hybrid"])
def test_suite_runs_in_common_workflow(model, model_study):
    from music_recsys.experiment_search import smoke, run_search, select_best
    config = model_study(model)
    assert smoke(config)["status"] == "succeeded"
    first = run_search(config)
    assert first["status"] == "succeeded"
    assert run_search(config)["reused_trials"] == first["planned_trials"]
    assert select_best(config)["model"] == model
```

同じtest内でinspect_selected→選択パラメータの実推薦→review→freeze→Test→再利用まで検証する。別testでmetric未指定ならselectとTestが失敗し、trial_curvesは読めることを確認する。

- [ ] Step 2: `.venv/bin/python -m pytest -q tests/test_model_suite_workflow.py tests/test_model_suite_notebook.py`。Expected: 新モデル・追加Reader経路の未対応でRED。
- [ ] Step 3: 共通Runnerの学習前に特徴を一度検証してモデルとEvaluatorへ渡す。v2のBeyond-Accuracyをtask/Kで結び、選択表と精度×ILD / Novelty・Coverageへ表示する。保存物の追加指標がない場合は未測定表示にする。
- [ ] Step 4: 03でfeaturesを選択contextから渡し、モデル再入力を不要にする。01で選択特徴の欠損率・出典、02でモデル別コスト・タスク・条件を表示する。新規モデルと特徴ありの02/03をfresh kernelで確認する。Expected: 全統合テストGREEN、Test入力を渡す環境変数があっても03はValidationだけを使う。
- [ ] Step 5: `feat: 全従来モデルを探索とNotebookに接続`としてコミット。

## Task 7: 実データValidation・全体検証・文書

**Files:** Create real Validation診断用TOML、`docs/models.md`、`experiments/music4all/model-suite-validation.md`; Modify README / evaluation関連文書 / CONTRIBUTING / この計画。生データと実行済みNotebookはGit管理外へ置く。

**Interfaces:** Task 1のSnapshot特徴export、既存temporal v2 export、共通Search CLI、追加trial_curves、全7モデル。

- [ ] Step 1: 既存100ユーザーSnapshot / Splitを検証し、新しいValidation bundleと特徴artifactを生成する。DB起動状態を記録し、必要なら起動、終了時に元へ戻す。認証値はログへ出さない。

```bash
uv run --locked --no-sync --env-file .env python -m music_recsys.temporal_cli export --split-id 7d50f6c9-6835-4919-be91-1293ae0a6f1d --phase validation --protocol listening-v2 --output data/benchmarks/model-suite/validation
uv run --locked --no-sync --env-file .env python -m music_recsys.features --snapshot-id 3b3115c3-5958-44fe-9705-31f647db8ac2 --output data/benchmarks/model-suite/features.json
```

Expected: 新規出力、出典とhash一致。学習100ユーザー・83,745イベント・12,664曲。件数が異なれば同条件とはみなさず原因を調べる。

- [ ] Step 2: モデルごとのTOMLでseed 0 / 42、主期間90日、K1〜20、λ1（補助0 / .5 / 2）のValidation診断を実行する。主指標は空欄として自動選択を止め、実在するパラメータの小さなgridから始める。実データを見て研究上の主指標を事後選択しない。

```bash
.venv/bin/python -m music_recsys.experiment_search --config configs/workflow-music4all-usercf.toml smoke
.venv/bin/python -m music_recsys.experiment_search --config configs/workflow-music4all-usercf.toml search
```

Expected: 実測結果保存・成功再利用。評価可能人数が少ない場合はその値を報告し、データ規模を暗黙に変えない。実測したTraining Time・Latency P50/P95・指標・特徴欠損を表にする。

- [ ] Step 3: 全通常テスト、隔離DB、大規模生成、Ruff、buildを実行する。

```bash
MPLCONFIGDIR=/private/tmp/music-recsys-mpl-cache .venv/bin/python -m pytest -q
MUSIC_RECSYS_TEST_DB=1 uv run --locked --no-sync --env-file .env python -m pytest -q tests/db
MUSIC_RECSYS_TEST_SCALE=1 .venv/bin/python -m pytest -q tests/test_temporal_scale.py tests/test_listening_scale.py
.venv/bin/ruff check src tests notebooks migrations
.venv/bin/ruff format --check src tests notebooks migrations
uv build
git diff --check
```

Expected: 必須チェック成功。任意チェックの実行可否と理由を区別し、未実行を成功扱いにしない。図をPNGへ保存してタイトル・軸・条件・未測定表示を目視確認する。

- [ ] Step 4: 独立した最終レビュー1回を依頼する。対象は計画開始時のcommitから全差分。重要指摘は再現テストRED→GREENで修正し、全体回帰を行う。保留LLM / Agentを未完了の従来モデルとして報告しない。
- [ ] Step 5: 定義・使い方・測定結果・再現コマンド・限界を日本語で更新する。`docs: 従来モデルの検証結果と使用手順を記録`としてコミットし、全論理コミットをpushする。HEADとoriginの一致・作業ツリーを確認する。

## 実行方法の提案

このセッションで担当agentが順に実装し、最後に独立レビューを1回行う方法を推奨する。特徴・モデル・保存形式の依存を一貫して扱え、各タスクに別の実装agentを割り当てる方法よりコンテキストの重複を抑えられる。実装計画のレビュー後に開始する。
