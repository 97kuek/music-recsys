from contextlib import contextmanager
from types import SimpleNamespace

import matplotlib
import numpy as np
import pandas as pd
import pytest

matplotlib.use("Agg")


def test_grid_draws_only_measured_metric_and_keeps_missing_cell(tmp_path):
    from PIL import Image

    from backend.interfaces.notebook.parameter_figures import draw_grid

    rows = [
        dict(task="discovery", metric="recall", k=20, neighbors=20, k1=k1, b=b, mean=value)
        for k1, b, value in [(0.3, 0, 0.02), (0.3, 1, None), (1.2, 0, 0.03), (1.2, 1, 0.04)]
    ]
    rows.append(
        dict(task="all", metric="training_seconds", k=np.nan, neighbors=20, k1=0.3, b=0, mean=None)
    )
    from backend.infrastructure.research.reports import FigureWriter
    from backend.interfaces.notebook.plots import figure_scope

    @contextmanager
    def draw(frame):
        with figure_scope():
            yield draw_grid(frame)

    figures = FigureWriter(draw, dpi=300, pdf=True).write(tmp_path, pd.DataFrame(rows))
    assert figures == ["recall_n20.png"]
    assert (tmp_path / "recall_n20.pdf").is_file()
    with Image.open(tmp_path / figures[0]) as image:
        assert image.width >= 1800


def test_grid_rejects_duplicate_conditions(tmp_path):
    from backend.interfaces.notebook.parameter_figures import draw_grid

    row = dict(task="discovery", metric="recall", k=20, neighbors=20, k1=1.2, b=0.5, mean=0)
    with pytest.raises(ValueError, match="Duplicate"):
        list(draw_grid(pd.DataFrame([row, row])))


def test_primary_does_not_mix_windows_or_day_weights():
    from backend.interfaces.notebook.parameter_figures import primary

    frame = pd.DataFrame(dict(window_days=[30, 90, 90, np.nan], day_weight=[np.nan, 1, 2, np.nan]))
    selected = primary(
        frame,
        SimpleNamespace(evaluation=SimpleNamespace(primary_window_days=90, primary_day_weight=1)),
    )
    assert selected.index.tolist() == [1, 3]


def test_audit_representatives_are_declared_before_results_exist():
    from backend.infrastructure.research.search import load_study_config
    from backend.interfaces.cli.content_bm25_validation import audit_sources

    sources = audit_sources(
        load_study_config("configs/content/sensitivity.json"),
        load_study_config("configs/bm25/grid.json"),
    )
    assert len(sources) == 10
    assert next(iter(sources)) == "bm25-n20-k1.2-b0.75"
    assert len({str(p) for p in sources.values()}) == 10


def test_publication_draw_is_read_only_and_keeps_zero_but_omits_missing_groups(tmp_path):
    import matplotlib.pyplot as plt

    from backend.interfaces.notebook.parameter_figures import draw

    config = SimpleNamespace(
        model="bm25",
        evaluation=SimpleNamespace(primary_window_days=90, primary_day_weight=1, ks=(20,)),
    )
    frame = pd.DataFrame(
        [
            dict(
                condition=str(n),
                task="discovery",
                metric="recall",
                k=20,
                neighbors=n,
                k1=0.3,
                b=0.0,
                mean=value,
                window_days=90,
                day_weight=np.nan,
            )
            for n, value in [(10, None), (20, 0.0)]
        ]
    )
    before = set(plt.get_fignums())
    with draw(frame, config) as figures:
        names = [name for name, figure in figures]
        assert names == ["recall_n20.png"]
        assert not list(tmp_path.iterdir())
    assert set(plt.get_fignums()) == before


@pytest.mark.parametrize("extension", [".png", ".pdf"])
def test_publication_write_failure_closes_streamed_figures(tmp_path, monkeypatch, extension):
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    from backend.infrastructure.research.reports import FigureWriter
    from backend.interfaces.notebook.parameter_figures import draw

    config = SimpleNamespace(
        model="content",
        evaluation=SimpleNamespace(primary_window_days=90, primary_day_weight=1, ks=(20,)),
    )
    frame = pd.DataFrame(
        [
            dict(
                condition="one",
                task="discovery",
                metric="recall",
                k=20,
                use_idf=True,
                aggregation="binary",
                mean=0.0,
                ci_low=np.nan,
                ci_high=np.nan,
                window_days=90,
                day_weight=np.nan,
            )
        ]
    )
    saved = Figure.savefig

    def fail(self, filename, **kwargs):
        if str(filename).endswith(extension):
            raise OSError("disk full")
        return saved(self, filename, **kwargs)

    monkeypatch.setattr(Figure, "savefig", fail)
    before = set(plt.get_fignums())
    with pytest.raises(OSError, match="disk full"):
        FigureWriter(draw, pdf=True).write(tmp_path, frame, config)
    assert set(plt.get_fignums()) == before
