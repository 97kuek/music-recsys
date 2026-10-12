"""Notebookを新しいカーネルで上から実行し、同じ評価結果になることを確認する。"""

import ast
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "name", ["01_popularity_benchmark.ipynb", "02_music4all_data_profile.ipynb"]
)
def test_notebook_python_cells_compile(name):
    path = Path(__file__).resolve().parents[1] / "notebooks" / name
    notebook = json.loads(path.read_text())
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            ast.parse("".join(cell["source"]))


@pytest.mark.notebook
def test_notebook_runs_from_fresh_kernel(tmp_path):
    root = Path(__file__).resolve().parents[1]
    path = root / "notebooks/01_popularity_benchmark.ipynb"
    assert path.is_file()
    nbformat = pytest.importorskip("nbformat", reason="install the notebook dependency group")
    nbclient = pytest.importorskip("nbclient", reason="install the notebook dependency group")
    notebook = nbformat.read(path, as_version=4)
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "assert report.summary.n_requests == 5\n"
            "assert report.summary.n_evaluated == 4\n"
            "assert report.summary.recall == 0.625\n"
            "assert abs(report.summary.ndcg - 0.561019236584229) < 1e-6\n"
            "assert RUN_DIR.joinpath('results.json').is_file()\n"
        )
    )
    executed = nbclient.NotebookClient(
        notebook,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(root / "notebooks")}},
    ).execute()
    nbformat.write(executed, tmp_path / "executed.ipynb")


@pytest.mark.notebook
def test_profile_notebook_reads_saved_aggregates(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    path = root / "notebooks/02_music4all_data_profile.ipynb"
    assert path.is_file()
    nbformat = pytest.importorskip("nbformat", reason="install the notebook dependency group")
    nbclient = pytest.importorskip("nbclient", reason="install the notebook dependency group")
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
                "provenance": {"source_sha256": "test-fixture"},
            }
        )
    )
    monkeypatch.setenv("MUSIC_RECSYS_PROFILE", str(profile_path))
    notebook = nbformat.read(path, as_version=4)
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "assert profile['n_events'] == 10\nassert profile['split_events']['train'] == 8"
        )
    )
    executed = nbclient.NotebookClient(
        notebook,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(root / "notebooks")}},
    ).execute()
    nbformat.write(executed, tmp_path / "executed-profile.ipynb")
