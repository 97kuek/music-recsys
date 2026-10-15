from pathlib import Path

import pytest
from test_listening_workflow import prepared

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.notebook
@pytest.mark.parametrize(
    "name,finalize",
    [
        ("01_data.ipynb", False),
        ("02_analysis.ipynb", False),
        ("03_recommendation.ipynb", False),
        ("02_analysis.ipynb", True),
    ],
)
def test_v2_notebooks_run_from_selected_artifact(
    listening_study, tmp_path, monkeypatch, name, finalize
):
    import nbformat
    from nbclient import NotebookClient

    from music_recsys.experiment_search import freeze_selection, review_selection, run_final_test

    config, _ = prepared(listening_study)
    if finalize:
        review_selection(config, accepted=True)
        freeze_selection(config)
        run_final_test(config)
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(config.path))
    monkeypatch.setenv("MUSIC_RECSYS_DATASET_PATH", str(config.test_dataset))
    notebook = nbformat.read(ROOT / "notebooks" / name, as_version=4)
    from notebook_support import choose_config

    choose_config(notebook, config.path)
    if name.startswith("01"):
        check = 'assert not observation_df.empty\nassert set(validation_requests_df.task)=={"new","repeat"}'
    elif name.startswith("02"):
        check = 'assert not workflow_curves.empty\nassert set(workflow_curves.task)=={"discovery","repeat","retention_proxy"}\nassert len(search_df)==4'
        if finalize:
            check += '\nassert not final_df.empty\nassert set(final_df.phase)=={"test"}'
    else:
        check = 'assert dataset.provenance["phase"]=="validation"\nassert result.request.task=="repeat"\nassert all(row["in_history"] for row in result.recommendations)\nassert MODEL_PARAMETERS==selected_context["parameters"]'
    notebook.cells.append(nbformat.v4.new_code_cell(check))
    executed = NotebookClient(
        notebook,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(ROOT / "notebooks")}},
    ).execute()
    nbformat.write(executed, tmp_path / name)
