import numpy as np
import pandas as pd
import pytest


def test_report_filter_keeps_zero_and_missing_estimates_without_mutation():
    from src.interfaces.notebook.report_data import accuracy_rows

    rows = [
        dict(
            task="repeat",
            metric="graded_ndcg",
            window_days=window,
            day_weight=weight,
            k=k,
            mean=mean,
        )
        for window, weight, k, mean in [
            (90, 1.0, 3, None),
            (90, 1.0, 2, 0),
            (30, 1.0, 2, 0.5),
            (90, 0.5, 2, 0.7),
        ]
    ]
    frame = accuracy_rows({"result": {"accuracy": rows}}, "repeat", "graded_ndcg")
    assert frame.k.tolist() == [2, 3]
    assert frame["mean"].iloc[0] == 0
    assert pd.isna(frame["mean"].iloc[1])
    assert rows[0]["mean"] is None


def test_shared_observations_reject_changed_counts_and_missing_bins():
    from src.interfaces.notebook.report_data import shared_observations

    rows = [
        dict(
            phase="test", window_days=90, task="new", observation="play_count", bin=str(i), pairs=i
        )
        for i in (1, 2)
    ]
    record = {"result": {"observations": rows}}
    reordered = {"result": {"observations": list(reversed(rows))}}
    assert shared_observations([record, reordered]) == rows
    for changed in ([rows[0]], [rows[0], dict(rows[1], pairs=3)]):
        with pytest.raises(ValueError, match="distributions differ"):
            shared_observations([record, {"result": {"observations": changed}}])


def test_discrete_cutoffs_preserve_missing_values_and_pointwise_intervals():
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.evaluation_figures import accuracy_figures

    frame = pd.DataFrame(
        [
            dict(
                task="discovery",
                metric="recall",
                experiment="plays",
                k=k,
                mean=mean,
                ci_low=low,
                ci_high=high,
            )
            for k, mean, low, high in [(2, 0.01, 0.005, 0.015), (3, None, None, None), (4, 0, 0, 0)]
        ]
    )
    figures = accuracy_figures(frame)
    try:
        ax = figures["recall"].axes[0]
        assert not ax.get_title()
        assert ax.get_ylim()[0] == 0
        assert all(float(x).is_integer() for x in ax.get_xticks())
        np.testing.assert_equal(ax.lines[0].get_ydata(), [1.0, np.nan, 0.0])
        assert ax.lines[0].get_marker() == "o"
        assert ax.get_legend().get_texts()[0].get_text() == "Total play count"
        assert "(%)" in ax.get_ylabel()
        # Confidence intervals are segments at observed K, not a continuous band.
        segments = ax.containers[0].lines[2][0].get_segments()
        np.testing.assert_allclose(segments[0], [[2, 0.5], [2, 1.5]])
        assert len(segments) == 2
    finally:
        plt.close("all")


def test_binned_distributions_use_task_denominators_and_require_measured_bins():
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.evaluation_figures import observation_figures

    bins = ["1", "2", "3–4", "5–9", "10–19", "20+"]
    frame = pd.DataFrame(
        [
            dict(
                phase="test",
                window_days=90,
                task=task,
                observation="evaluation_play_count",
                bin=bin,
                pairs=(i + 1) * scale,
            )
            for task, scale in [("new", 1), ("repeat", 2)]
            for i, bin in enumerate(bins)
        ]
    )
    try:
        ax = observation_figures(frame)["play_count"].axes[0]
        heights = [bar.get_height() for bar in ax.patches]
        assert sum(heights[:6]) == pytest.approx(100)
        np.testing.assert_allclose(heights[:6], heights[6:])
        assert ax.get_ylim()[0] == 0 and not ax.get_title()
        assert "21" in ax.get_legend().get_texts()[0].get_text()
        assert "42" in ax.get_legend().get_texts()[1].get_text()
        with pytest.raises(ValueError, match="missing is not zero"):
            observation_figures(frame.iloc[1:])
        with pytest.raises(ValueError, match="one observation window"):
            observation_figures(pd.concat([frame, frame.assign(window_days=30)]))
        assert observation_figures(frame.assign(pairs=0)) == {}
    finally:
        plt.close("all")


def test_heatmap_is_not_interpolated_and_missing_cells_remain_missing():
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.evaluation_figures import accuracy_heatmaps

    frame = pd.DataFrame(
        [
            dict(task="discovery", metric="recall", window_days=window, k=k, mean=mean)
            for window, k, mean in [(30, 2, 0.01), (30, 3, None), (90, 2, 0.02), (90, 3, 0)]
        ]
    )
    try:
        ax = accuracy_heatmaps(frame, 1)["recall_heatmap"].axes[0]
        image = ax.images[0]
        assert image.get_interpolation() == "nearest"
        assert image.get_array().mask[0, 1]
        assert image.get_array()[1, 1] == 0
        assert image.get_array()[0, 0] == 1
        assert not ax.get_title()
    finally:
        plt.close("all")
