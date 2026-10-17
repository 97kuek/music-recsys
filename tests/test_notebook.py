"""現行Notebookの明示入力・読込み専用既定・欠損時の動作を確認する。"""

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted(p.name for p in (ROOT / "notebooks").glob("*.ipynb"))


def _run_notebook_cells(name, count=None, config_path=None):
    notebook = json.loads((ROOT / "notebooks" / name).read_text())
    codes = [c for c in notebook["cells"] if c["cell_type"] == "code"]
    namespace = {}
    for cell in codes[:count]:
        exec(compile("".join(cell["source"]), name, "exec"), namespace)
        if config_path is not None and "config-picker" in cell["metadata"].get("tags", []):
            picker = namespace["config_picker"]
            picker.dropdown.options = [("未選択", None), ("test", str(config_path))]
            picker.dropdown.value = str(config_path)
            picker.load_button.click()
    return namespace


@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebook_structure_and_no_implicit_legacy_inputs(name):
    notebook = json.loads((ROOT / "notebooks" / name).read_text())
    for index, cell in enumerate(notebook["cells"]):
        source = "".join(cell["source"])
        assert "。" not in source
        if cell["cell_type"] == "code":
            ast.parse(source)
            assert cell["execution_count"] is None and cell["outputs"] == []
            explanation = "".join(notebook["cells"][index - 1]["source"])
            assert explanation.startswith("## ")
            for label in ("処理:", "入力:", "出力:", "次:"):
                assert label in explanation
            for legacy in (
                "REFERENCE_MODE",
                "REPORT_PATHS",
                "workflow-toy.toml",
                "examples/toy",
                "MUSIC_RECSYS_SNAPSHOT_ID",
                "experiments/music4all",
            ):
                assert legacy not in source
    markdown_lines = sum(
        len(c["source"]) for c in notebook["cells"] if c["cell_type"] == "markdown"
    )
    assert markdown_lines <= 200


def test_analysis_setup_loads_settings_without_evaluating(listening_study, monkeypatch):
    import src.evaluation.benchmark as benchmark
    import src.experiment_search as search
    import src.search_analysis as analysis

    config = listening_study()
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(config.path))

    def forbidden(*args, **kwargs):
        raise AssertionError("setup must not load evaluation data or run a model")

    monkeypatch.setattr(search, "context", forbidden)
    monkeypatch.setattr(analysis, "context", forbidden)
    monkeypatch.setattr(benchmark, "run_benchmark", forbidden)
    ns = _run_notebook_cells("02_analysis.ipynb", 3, config.path)
    assert ns["WORKFLOW"].path == config.path
    assert not config.output.exists()


def test_01_missing_input_does_not_fall_back(listening_study, monkeypatch):
    config = listening_study()
    path = config.path
    path.write_text(
        path.read_text().replace('validation_dataset="validation"', 'validation_dataset="missing"')
    )
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(path))
    with pytest.raises(FileNotFoundError, match="未準備"):
        _run_notebook_cells("01_data.ipynb", 4, path)


def test_03_missing_selection_does_not_fall_back(listening_study, monkeypatch):
    config = listening_study()
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(config.path))
    with pytest.raises(ValueError, match="未選択"):
        _run_notebook_cells("03_recommendation.ipynb", 4, config.path)
    assert not config.output.exists()


def test_03_uses_selected_validation_even_with_other_dataset_environment(
    listening_study, monkeypatch
):
    from test_listening_workflow import prepared

    config, selected = prepared(listening_study)
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(config.path))
    monkeypatch.setenv("MUSIC_RECSYS_DATASET_PATH", str(config.test_dataset))
    ns = _run_notebook_cells("03_recommendation.ipynb", 5, config.path)
    assert ns["DATASET_PATH"] == config.validation_dataset
    assert ns["MODEL_PARAMETERS"] == selected["parameters"]
    assert ns["MODEL_SEED"] == selected["seeds"][0]
    assert ns["dataset"].provenance["phase"] == "validation"
    assert ns["result"].request.task == "repeat"


@pytest.mark.notebook
def test_unexecuted_analysis_runs_without_loading_test(listening_study, monkeypatch, tmp_path):
    import nbformat
    from nbclient import NotebookClient

    config = listening_study(metric=None)
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(config.path))
    nb = nbformat.read(ROOT / "notebooks/02_analysis.ipynb", as_version=4)
    from notebook_support import choose_config

    choose_config(nb, config.path)
    nb.cells.insert(
        0,
        nbformat.v4.new_code_cell(
            "import src.evaluation.benchmark as benchmark\n"
            "def forbidden(*args, **kwargs):\n    raise AssertionError('read only')\n"
            "benchmark.run_benchmark = forbidden"
        ),
    )
    nb.cells.append(
        nbformat.v4.new_code_cell(
            "assert search_df.empty\nassert workflow_curves.empty\nassert final_df.empty\n"
            "assert not WORKFLOW.output.exists()"
        )
    )
    executed = NotebookClient(
        nb,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(ROOT / "notebooks")}},
    ).execute()
    nbformat.write(executed, tmp_path / "analysis-unexecuted.ipynb")
