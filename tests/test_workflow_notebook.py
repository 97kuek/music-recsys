"""選択artifactを使う新しい02→03の実行経路を確認する。"""

import inspect
from pathlib import Path

import pytest
from test_experiment_search import fixture_factory
from test_notebook import _run_notebook_cells

ROOT = Path(__file__).resolve().parents[1]


def test_03_uses_selected_configuration_even_when_other_dataset_is_in_environment(
    study, monkeypatch
):
    import music_recsys.recommendation_inspection as inspection
    from music_recsys.experiment_search import run_search, select_best, smoke

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    selected = select_best(config)
    monkeypatch.setattr(inspection, "create_recommender", fixture_factory)
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(config.path))
    monkeypatch.setenv("MUSIC_RECSYS_DATASET_PATH", str(config.test_dataset))
    ns = _run_notebook_cells("03_recommendation.ipynb", [2, 4])
    assert ns["DATASET_PATH"] == config.validation_dataset
    assert ns["MODEL_PARAMETERS"] == selected["parameters"]
    assert ns["MODEL_SEED"] == selected["seeds"][0]
    assert ns["dataset"].provenance["phase"] == "validation"


@pytest.mark.notebook
@pytest.mark.parametrize(
    "name,finalize",
    [("02_analysis.ipynb", False), ("03_recommendation.ipynb", False), ("02_analysis.ipynb", True)],
)
def test_selected_workflow_notebooks_run_from_fresh_kernel(
    study, tmp_path, monkeypatch, name, finalize
):
    import nbformat
    from nbclient import NotebookClient

    from music_recsys.experiment_search import run_search, select_best, smoke

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    select_best(config)
    if finalize:
        from music_recsys.experiment_search import (
            freeze_selection,
            review_selection,
            run_final_test,
        )

        review_selection(config, accepted=True)
        freeze_selection(config)
        run_final_test(config, factory=fixture_factory)
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(config.path))
    nb = nbformat.read(ROOT / "notebooks" / name, as_version=4)
    if name == "03_recommendation.ipynb":
        prefix = (
            inspect.getsource(fixture_factory)
            + "\nimport music_recsys.recommendation_inspection as inspection\ninspection.create_recommender=fixture_factory"
        )
        checks = 'assert selected_context is not None\nassert dataset.provenance["phase"]=="validation"\nassert MODEL_PARAMETERS==selected_context["parameters"]'
    else:
        prefix = "import music_recsys.evaluation.benchmark as benchmark\ndef forbidden(*args,**kwargs):\n    raise AssertionError('analysis must not execute trials by default')\nbenchmark.run_benchmark=forbidden"
        checks = "assert not REFERENCE_MODE\nassert len(search_df)==4\nassert set(workflow_curves.k)==set(range(1,21))\nassert final_df.empty"
        if finalize:
            checks = checks.replace(
                "assert final_df.empty",
                'assert not final_df.empty\nassert set(final_df.phase)=={"test"}\nassert set(final_df.k)==set(range(1,21))',
            )
    nb.cells.insert(0, nbformat.v4.new_code_cell(prefix))
    nb.cells.append(nbformat.v4.new_code_cell(checks))
    executed = NotebookClient(
        nb,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(ROOT / "notebooks")}},
    ).execute()
    nbformat.write(executed, tmp_path / name)
