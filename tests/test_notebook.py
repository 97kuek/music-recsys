"""3冊を独立したカーネルで実行し、外部サービスなしの分析を検証する。"""

import ast
import json
from pathlib import Path

import pytest

NOTEBOOKS = ["01_data.ipynb", "02_recommendation.ipynb", "03_analysis.ipynb"]
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebook_python_cells_compile_and_outputs_are_clear(name):
    notebook = json.loads((ROOT / "notebooks" / name).read_text())
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            ast.parse("".join(cell["source"]))
            assert cell["execution_count"] is None
            assert cell["outputs"] == []


@pytest.mark.notebook
@pytest.mark.parametrize(
    "name,empty_validation", [(name, False) for name in NOTEBOOKS] + [("01_data.ipynb", True)]
)
def test_notebook_runs_from_fresh_kernel(name, empty_validation, tmp_path, monkeypatch):
    nbformat = pytest.importorskip("nbformat", reason="install the notebook dependency group")
    nbclient = pytest.importorskip("nbclient", reason="install the notebook dependency group")
    # 環境変数で記録が有効でも、既定Notebookは常駐MLflowに接続しない。
    monkeypatch.setenv("MUSIC_RECSYS_TRACKING", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:1")
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "n_events": 10,
                "n_users": 1,
                "n_items": 1,
                "min_time": "2020-01-01T12:00:00",
                "max_time": "2020-01-10T12:00:00",
                "boundaries": {
                    "train_end": "2020-01-09T00:00:00",
                    "validation_end": "2020-01-10T00:00:00",
                },
                "split_events": {"train": 8, "validation": 1, "test": 1},
                "split_fractions": {"train": 0.8, "validation": 0.1, "test": 0.1},
                "daily_counts": [
                    {"day": f"2020-01-{day:02d}", "n_events": 1} for day in range(1, 11)
                ],
                "train": {"n_users": 1, "n_items": 1, "unique_tracks_quantiles": {"0.5": 1.0}},
                **{
                    key: {
                        "novel_users": 0,
                        "novel_pairs": 0,
                        "pair_reasons": {"seen": 1},
                        "users_with_truth_by_min_history": {"1": 0},
                    }
                    for key in ("validation", "test_train_only", "test_refit_train_validation")
                },
                "provenance": {"source_sha256": "synthetic-test-fixture"},
            }
        )
    )
    if empty_validation:
        profile = json.loads(profile_path.read_text())
        profile["validation"]["pair_reasons"] = {}
        profile["split_events"].update({"validation": 0, "test": 2})
        profile["split_fractions"].update({"validation": 0.0, "test": 0.2})
        profile["daily_counts"] = [
            row for row in profile["daily_counts"] if row["day"] != "2020-01-09"
        ]
        profile["daily_counts"][-1]["n_events"] = 2
        profile_path.write_text(json.dumps(profile))
    monkeypatch.setenv("MUSIC_RECSYS_PROFILE", str(profile_path))
    assertions = {
        "01_data.ipynb": "assert profile['n_events'] == 10\nassert snapshot.mode == 'fixture'\nassert tracks_df.shape[0] == 4 and users_df.shape[0] == 2\nassert SNAPSHOT_ID is None\nassert all(row['passed'] is not False for row in validation)\nassert snapshot.database_checks is None\nassert candidates['events'][0]['n_events'] == 8",
        "02_recommendation.ipynb": "assert result.request.user_id == 'u1'\nassert result.response.item_ids == ('track_c', 'track_d')\nassert history_df.item_id.tolist() == ['track_a', 'track_b']\nassert recommendations_df.score.tolist() == [2, 1]\nassert not recommendations_df.in_history.any()\nassert player is None",
        "03_analysis.ipynb": 'assert len(reports) == 1\nassert performance_df.query(\'metric == "recall"\')["mean"].iloc[0] == 0.625\nassert performance_df.n_users.tolist() == [4, 4]\nassert performance_df.ci_low.notna().all()\nassert efficiency_df.cost_total.isna().all()\nassert not MLFLOW_RUNS',
    }
    notebook = nbformat.read(ROOT / "notebooks" / name, as_version=4)
    if name == "03_analysis.ipynb":
        notebook.cells.insert(
            0,
            nbformat.v4.new_code_cell(
                "import music_recsys.evaluation.benchmark as benchmark\n"
                "def forbidden(*args, **kwargs):\n    raise AssertionError('03 must only read saved results')\n"
                "benchmark.run_experiment = forbidden\nbenchmark.run_benchmark = forbidden\n"
                "benchmark.save_report = forbidden\n"
            ),
        )
    notebook.cells.append(nbformat.v4.new_code_cell(assertions[name]))
    executed = nbclient.NotebookClient(
        notebook,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(ROOT / "notebooks")}},
    ).execute()
    assert any(
        "image/png" in output.get("data", {})
        for cell in executed.cells
        for output in cell.get("outputs", [])
    )
    nbformat.write(executed, tmp_path / name)


@pytest.mark.notebook
@pytest.mark.parametrize("case", ["aggregate", "undefined", "multiple", "mixed"])
def test_analysis_handles_saved_result_variants_without_experiment_execution(case, tmp_path):
    nbformat = pytest.importorskip("nbformat")
    nbclient = pytest.importorskip("nbclient")
    raw = json.loads((ROOT / "examples/reports/toy-popularity.json").read_text())
    if case in {"aggregate", "mixed"}:
        del raw["runs"]
        if case == "mixed":
            raw["summary"]["n_failed"] = 1
    elif case == "undefined":
        for run in raw["runs"]:
            run["evaluation"].update(ndcg=None, recall=None, relevant_count=0)
        raw["summary"].update(ndcg=None, recall=None, n_evaluated=0, n_excluded=5)
    path = tmp_path / "saved.json"
    path.write_text(json.dumps(raw))
    notebook = nbformat.read(ROOT / "notebooks/03_analysis.ipynb", as_version=4)
    config = next(
        cell for cell in notebook.cells if "configuration" in cell.metadata.get("tags", [])
    )
    config.source += f'\nREPORT_PATHS = {{"Popularity": Path({str(path)!r})}}\n'
    if case == "multiple":
        config.source += f'REPORT_PATHS["Repeat"] = Path({str(path)!r})\n'
    if case == "mixed":
        config.source += f'REPORT_PATHS = {{"Popularity": ROOT / "examples/reports/toy-popularity.json", "Aggregate": Path({str(path)!r})}}\n'
    notebook.cells.insert(
        0,
        nbformat.v4.new_code_cell(
            "import music_recsys.evaluation.benchmark as benchmark\n"
            "def forbidden(*args, **kwargs):\n    raise AssertionError('must only read saved results')\n"
            "benchmark.run_experiment = forbidden\nbenchmark.run_benchmark = forbidden\nbenchmark.save_report = forbidden\n"
        ),
    )
    checks = {
        "aggregate": "assert performance_df.ci_low.isna().all()\nassert users_df.empty\nassert efficiency_df.latency_p95.isna().all()",
        "undefined": 'assert performance_df["mean"].isna().all()\nassert performance_df.ci_low.isna().all()\nassert (performance_df.n_evaluated == 0).all()',
        "mixed": 'assert failure_summary_df.set_index("run").loc["Aggregate", "n_failed"] == 1\nassert len(performance_df) == 4\nassert len(users_df) == 5',
        "multiple": 'assert len(difference_df) == 1\nassert difference_df["mean"].iloc[0] == 0\nassert repetition_df.n_runs.iloc[0] == 2',
    }
    notebook.cells.append(nbformat.v4.new_code_cell(checks[case]))
    executed = nbclient.NotebookClient(
        notebook,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(ROOT / "notebooks")}},
    ).execute()
    nbformat.write(executed, tmp_path / f"analysis-{case}.ipynb")
