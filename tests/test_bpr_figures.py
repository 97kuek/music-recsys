import json

import matplotlib
import numpy as np
import pandas as pd
import pytest

matplotlib.use("Agg")

from src.interfaces.notebook.bpr_figures import figures, render


def points():
    return pd.DataFrame(
        [
            dict(factors=f, seed=s, training_seconds=t, binary_ndcg=n, recall=n / 2)
            for f, values in [(32, [0, 0.01, 0.02]), (64, [0.02, 0.04, 0.06])]
            for s, t, n in zip([42, 43, 44], [10, 20, 30], values, strict=True)
        ]
    )


def test_factor_errorbars_are_seed_sample_sd_and_seed_plot_has_three_lines():
    import matplotlib.pyplot as plt

    images = figures(points(), epochs=100, window_days=90, k=20)
    try:
        assert len(images) == 4
        assert all(len(fig.axes) == 1 for fig in images.values())
        for filename, scale in [("ndcg_factors.png", 1), ("recall_factors.png", 0.5)]:
            ax = images[filename].axes[0]
            errorbar = ax.containers[0]
            np.testing.assert_allclose(
                np.asarray(errorbar.lines[0].get_ydata(), dtype=float),
                np.array([0.01, 0.04]) * scale,
            )
            segments = errorbar.lines[2][0].get_segments()
            np.testing.assert_allclose(segments[0][:, 1], np.array([0.0, 0.02]) * scale, atol=1e-15)
            np.testing.assert_allclose(segments[1][:, 1], np.array([0.02, 0.06]) * scale)
        lines = images["ndcg_seeds.png"].axes[0].lines
        assert [line.get_label() for line in lines] == ["Seed 42", "Seed 43", "Seed 44"]
        np.testing.assert_allclose(lines[0].get_xdata(), [32, 64])
        np.testing.assert_allclose(lines[0].get_ydata(), [0, 0.02])
        time_ax = images["time_ndcg.png"].axes[0]
        assert time_ax.get_xlabel() == "Training time (seconds)"
        np.testing.assert_allclose(
            np.asarray(time_ax.containers[0].lines[0].get_xdata(), dtype=float), [20]
        )
    finally:
        for fig in images.values():
            plt.close(fig)


def test_unmeasured_or_incomplete_seed_conditions_are_not_plotted_as_zero():
    frame = points()
    frame.loc[0, "binary_ndcg"] = np.nan
    with pytest.raises(ValueError, match="measured, finite"):
        figures(frame, epochs=100, window_days=90, k=20)
    with pytest.raises(ValueError, match="same multiple seeds"):
        figures(points().iloc[1:], epochs=100, window_days=90, k=20)


def test_render_preserves_results_and_guards_source_changes(tmp_path):
    from test_factor_study import small_study

    from src.infrastructure.datasets.listening_data import file_sha256
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli.factor_study import run_study

    path = small_study(tmp_path)
    output = run_study(path, minimum_free_gb=0)
    state = json.loads((output / "result.json").read_text())
    protected = [output / "metrics.csv", output / "config.json"]
    protected += [output / c["result"] for c in state["conditions"]]
    original = {p: p.read_bytes() for p in protected}
    images = render(path)
    assert all(p.is_file() for p in images)
    assert all(p.read_bytes() == content for p, content in original.items())
    updated = json.loads((output / "result.json").read_text())
    assert updated["conditions"] == state["conditions"]
    assert updated["bpr_figures"]["k"] == 20
    assert all(updated["bpr_figures"]["figures"][p.name] == file_sha256(p) for p in images)
    assert all("loss" not in p.name for p in images)
    root = (output / "result.json").read_bytes()
    with pytest.raises(FileExistsError):
        render(path)
    assert (output / "result.json").read_bytes() == root
    child = output / state["conditions"][0]["result"]
    child.write_bytes(child.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="result changed"):
        render(load_study_config(path).path)
    assert (output / "result.json").read_bytes() == root
