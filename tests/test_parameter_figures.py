from types import SimpleNamespace

import matplotlib
import numpy as np
import pandas as pd
import pytest

matplotlib.use("Agg")


def test_grid_draws_only_measured_metric_and_keeps_missing_cell(tmp_path):
    from PIL import Image

    from src.interfaces.notebook.parameter_figures import draw_grid

    rows = [
        dict(task="discovery", metric="recall", k=20, neighbors=20, k1=k1, b=b, mean=value)
        for k1, b, value in [(0.3, 0, 0.02), (0.3, 1, None), (1.2, 0, 0.03), (1.2, 1, 0.04)]
    ]
    rows.append(
        dict(task="all", metric="training_seconds", k=np.nan, neighbors=20, k1=0.3, b=0, mean=None)
    )
    figures = draw_grid(pd.DataFrame(rows), tmp_path)
    assert figures == ["recall_n20.png"]
    assert (tmp_path / "recall_n20.pdf").is_file()
    with Image.open(tmp_path / figures[0]) as image:
        assert image.width >= 1800


def test_grid_rejects_duplicate_conditions(tmp_path):
    from src.interfaces.notebook.parameter_figures import draw_grid

    row = dict(task="discovery", metric="recall", k=20, neighbors=20, k1=1.2, b=0.5, mean=0)
    with pytest.raises(ValueError, match="Duplicate"):
        draw_grid(pd.DataFrame([row, row]), tmp_path)


def test_primary_does_not_mix_windows_or_day_weights():
    from src.interfaces.notebook.parameter_figures import primary

    frame = pd.DataFrame(dict(window_days=[30, 90, 90, np.nan], day_weight=[np.nan, 1, 2, np.nan]))
    selected = primary(
        frame,
        SimpleNamespace(evaluation=SimpleNamespace(primary_window_days=90, primary_day_weight=1)),
    )
    assert selected.index.tolist() == [1, 3]


def test_audit_representatives_are_declared_before_results_exist():
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli.content_bm25_validation import audit_sources

    sources = audit_sources(
        load_study_config("configs/content-sensitivity.json"),
        load_study_config("configs/bm25-grid.json"),
    )
    assert len(sources) == 10
    assert next(iter(sources)) == "bm25-n20-k1.2-b0.75"
    assert len({str(p) for p in sources.values()}) == 10
