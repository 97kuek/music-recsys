from pathlib import Path

import pytest
from model_suite_support import model_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.notebook
@pytest.mark.parametrize(
    "model,name,select",
    [
        ("content", "03_recommendation.ipynb", True),
        ("hybrid", "03_recommendation.ipynb", True),
        ("content", "02_analysis.ipynb", True),
        ("als", "02_analysis.ipynb", False),
    ],
)
def test_new_models_and_diagnostic_views_run_in_fresh_kernels(
    listening_study, tmp_path, monkeypatch, model, name, select
):
    import nbformat
    from nbclient import NotebookClient

    from src.experiment_search import run_search, select_best, smoke

    config = model_config(listening_study(metric="graded_ndcg" if select else None), model)
    # Persist the programmatic test config so the fresh kernel uses exactly the same values.
    path = config.path.parent / "notebook.toml"
    body = (
        config.path.read_text()
        .replace('name="listening-grid"', f'name="{config.name}"')
        .replace('name="itemcf"', f'name="{model}"')
        .replace('output="runs"', f'output="{config.output}"')
    )
    body = body.replace(
        "neighbors=[1,2]", "\n".join(f"{k}={str(v).lower()}" for k, v in config.grid.items())
    )
    body = body.replace(
        'validation_dataset="validation"',
        f'validation_dataset="validation"\nfeatures="{config.features}"',
    )
    if config.parameters:
        body = body.replace(
            "[search]",
            "[model.parameters]\n"
            + "\n".join(f"{k}={v}" for k, v in config.parameters.items())
            + "\n[search]",
        )
    path.write_text(body)
    from src.experiment_search import load_study_config

    config = load_study_config(path)
    smoke(config)
    run_search(config)
    if select:
        select_best(config)
    monkeypatch.setenv("MUSIC_RECSYS_EXPERIMENT_CONFIG", str(path))
    monkeypatch.setenv("MUSIC_RECSYS_DATASET_PATH", str(config.test_dataset))
    notebook = nbformat.read(ROOT / "notebooks" / name, as_version=4)
    from notebook_support import choose_config

    choose_config(notebook, config.path)
    check = (
        "assert not recommendations_df.empty\nassert recommendations_df.reason.notna().all()"
        if name.startswith("03")
        else "assert not workflow_curves.empty\nassert not beyond_df.empty\nassert workflow_curves.novelty.notna().any()"
    )
    notebook.cells.append(nbformat.v4.new_code_cell(check))
    executed = NotebookClient(
        notebook,
        timeout=180,
        kernel_name="python3",
        resources={"metadata": {"path": str(ROOT / "notebooks")}},
    ).execute()
    nbformat.write(executed, tmp_path / name)
