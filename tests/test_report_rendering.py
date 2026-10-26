"""Report rendering leaves persistence to its writer and releases figures on failure."""

from types import SimpleNamespace

import pandas as pd
import pytest

from src.infrastructure.research.reports import FigureWriter
from src.interfaces.notebook.itemcf_report import draw


def comparison_frame(value, *, similarity="cosine"):
    return pd.DataFrame(
        [
            dict(task=task, metric=metric, similarity=similarity, neighbors=20, k=k, mean=value)
            for task, metric in [
                ("discovery", "recall"),
                ("repeat", "repeat_recall"),
                ("discovery", "binary_ndcg"),
            ]
            for k in (2, 20)
        ]
    )


def test_draw_does_not_persist_and_writer_keeps_measured_zero(tmp_path):
    import matplotlib.pyplot as plt

    existing = set(plt.get_fignums())
    config = SimpleNamespace(output=tmp_path)
    with draw(comparison_frame(0), config) as figures:
        assert figures and not list(tmp_path.iterdir())
    assert set(plt.get_fignums()) == existing
    assert FigureWriter(draw).write(tmp_path, comparison_frame(None), config) == []
    assert not list(tmp_path.iterdir())
    names = FigureWriter(draw).write(tmp_path, comparison_frame(0), config)
    assert len(names) == 9
    assert {p.name for p in tmp_path.iterdir()} == set(names)
    assert set(plt.get_fignums()) == existing


def test_failed_draw_releases_partial_figures_and_keeps_existing_figure(tmp_path):
    import matplotlib.pyplot as plt

    prior = plt.figure()
    existing = set(plt.get_fignums())
    try:
        with pytest.raises(KeyError):
            FigureWriter(draw).write(tmp_path, comparison_frame(1, similarity="unknown"), None)
        assert set(plt.get_fignums()) == existing
        assert not list(tmp_path.iterdir())
    finally:
        plt.close(prior)


def test_write_failure_releases_drawn_figures(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    existing = set(plt.get_fignums())

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Figure, "savefig", fail)
    with pytest.raises(OSError, match="disk full"):
        FigureWriter(draw).write(tmp_path, comparison_frame(0), None)
    assert set(plt.get_fignums()) == existing
    assert not list(tmp_path.iterdir())
