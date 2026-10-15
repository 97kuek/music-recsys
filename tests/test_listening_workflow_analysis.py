from dataclasses import replace

import pytest
from test_listening_workflow import prepared


def test_v2_curves_keep_task_window_gain_and_user_uncertainty(listening_study):
    from music_recsys.search_analysis import (
        history_performance,
        plot_performance,
        plot_search,
        selected_curves,
        validation_overview,
    )

    config, _ = prepared(listening_study)
    frame = selected_curves(config, resamples=100)
    assert set(frame.task) == {"discovery", "retention_proxy", "repeat"}
    assert set(frame.k) == set(range(1, 21))
    assert set(frame.window_days) == {30, 90, 180}
    assert set(frame.loc[frame.metric == "graded_ndcg", "day_weight"]) == {0.0, 1.0, 2.0}
    assert frame.loc[frame.metric == "binary_ndcg", "day_weight"].isna().all()
    assert frame.loc[frame["mean"].notna(), "n_users"].min() >= 1
    assert len(plot_performance(frame).axes) == 2
    _, rows, summary = validation_overview(config)
    assert {"new", "repeat"} == set(rows.task)
    assert summary["phase"] == "validation"
    history = history_performance(config)
    assert set(history.metric) == {"graded_ndcg"}
    title = plot_search(config).axes[0].get_title()
    for condition in ("listening-v2", "repeat", "90 days", "day weight 1"):
        assert condition in title


def test_v2_final_comparison_does_not_mix_protocol_or_gain(listening_study):
    from music_recsys.experiment_search import (
        freeze_selection,
        review_selection,
        run_final_test,
        run_search,
        select_best,
        smoke,
    )
    from music_recsys.search_analysis import final_comparison, plot_final

    config, _ = prepared(listening_study)
    review_selection(config, accepted=True)
    freeze_selection(config)
    run_final_test(config)
    baseline = replace(
        config,
        name="popularity-listening",
        model="popularity",
        grid={},
        output=config.output.parent / "baseline",
    )
    smoke(baseline)
    run_search(baseline)
    select_best(baseline)
    review_selection(baseline, accepted=True)
    freeze_selection(baseline)
    run_final_test(baseline)
    frame = final_comparison([config, baseline])
    assert set(frame.phase) == {"test"}
    assert set(frame.experiment) == {"listening-grid", "popularity-listening"}
    assert set(frame.task) == {"discovery", "retention_proxy", "repeat"}
    axes = plot_final(frame).axes
    assert len(axes[0].lines) == 6
    assert len(axes[1].lines) == 4
    for ax in axes:
        for line in ax.lines:
            assert len(line.get_xdata()) == len(set(line.get_xdata())) == 20
    changed = replace(config, evaluation=replace(config.evaluation, primary_day_weight=2.0))
    with pytest.raises(ValueError, match="条件"):
        final_comparison([config, changed])


def test_repeat_inspection_counts_eligible_history_and_explains_seen_candidates(listening_study):
    from music_recsys.experiment_search import inspect_selected
    from music_recsys.recommendation_inspection import inspect_recommendation, request_options

    config, _ = prepared(listening_study)
    context = inspect_selected(config)
    dataset = context["dataset"]
    assert all(
        row["candidate_count"] > 0 and row["task"] == "repeat" for row in request_options(dataset)
    )
    result = inspect_recommendation(
        dataset, model="popularity", request_id=dataset.requests[0].request_id
    )
    assert all(row["in_history"] for row in result.recommendations)
    assert "既聴" in result.recommendations[0]["reason"]


def test_secondary_sensitivity_can_skip_bootstrap_without_imputing_a_ci():
    from music_recsys.experiment_analysis import BootstrapConfig, cluster_statistics

    statistics = cluster_statistics(
        [("u", 0.0), ("v", 1.0)], BootstrapConfig(resamples=100), compute_ci=False
    )
    assert statistics["mean"] == 0.5
    assert statistics["ci_low"] is None
    assert statistics["ci_method"] == "not_requested"


def test_seven_model_plot_separates_metrics_and_keeps_legend_off_curves():
    import matplotlib.pyplot as plt
    import pandas as pd

    from music_recsys.search_analysis import plot_final

    metrics = [
        ("discovery", "binary_ndcg"),
        ("discovery", "recall"),
        ("retention_proxy", "graded_ndcg"),
        ("repeat", "graded_ndcg"),
        ("repeat", "repeat_recall"),
    ]
    frame = pd.DataFrame(
        [
            dict(
                experiment=f"model-{model}",
                task=task,
                metric=metric,
                k=k,
                window_days=90,
                day_weight=1 if metric == "graded_ndcg" else None,
                mean=0.4,
                ci_low=0.3,
                ci_high=0.5,
            )
            for model in range(7)
            for task, metric in metrics
            for k in range(1, 21)
        ]
    )
    frame.attrs["primary"] = {"window_days": 90, "day_weight": 1}
    fig = plot_final(frame)
    plotted = [ax for ax in fig.axes if ax.lines]
    assert len(plotted) == 5
    assert all(len(ax.lines) == 7 and ax.get_legend() is None for ax in plotted)
    assert all("Test" in ax.get_title() for ax in plotted)
    legends = [ax.get_legend() for ax in fig.axes if ax.get_legend() is not None]
    assert len(legends) == 1
    assert {t.get_text() for t in legends[0].texts} == {f"model-{m}" for m in range(7)}
    plt.close(fig)
