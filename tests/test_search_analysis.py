"""探索画面は実測値だけを表示し、未完了を最適化完了にしない。"""

import json

import matplotlib
import pytest

matplotlib.use("Agg")

from test_experiment_search import fixture_factory


def test_state_guides_smoke_selection_and_review(study):
    from src.experiment_search import run_search, select_best, smoke
    from src.search_analysis import workflow_state

    config = study()
    assert workflow_state(config)["status"] == "not_run"
    smoke(config, factory=fixture_factory)
    assert "search" in workflow_state(config)["next_action"]
    run_search(config, factory=fixture_factory)
    assert workflow_state(config)["status"] == "selection_pending"
    select_best(config)
    assert workflow_state(config)["status"] == "review_pending"
    assert "03_recommendation" in workflow_state(config)["next_action"]


@pytest.mark.parametrize(
    "grid,stage,kind",
    [
        ("reverse=[false,true]", "A", "line"),
        ("reverse=[false,true]\noffset=[0,1]", "B", "heatmap"),
    ],
)
def test_grid_plots_actual_trials(study, grid, stage, kind):
    from src.experiment_search import run_search, smoke
    from src.search_analysis import plot_search, search_table

    config = study(grid=grid, stage=stage)
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    table = search_table(config)
    assert table["ndcg"].notna().all()
    fig = plot_search(config)
    assert "Validation" in fig.axes[0].get_title()
    assert bool(fig.axes[0].lines) if kind == "line" else bool(fig.axes[0].images)


def test_selected_curves_include_all_k_and_real_uncertainty(study):
    from src.experiment_search import run_search, select_best, smoke
    from src.search_analysis import selected_curves

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    select_best(config)
    frame = selected_curves(config, resamples=100)
    assert set(frame.k) == set(range(1, 21))
    assert frame.ci_low.notna().all()
    assert frame.graded_ndcg.isna().all()
    assert frame.api_cost.isna().all()
    assert frame.latency_p95.notna().all()


def test_mismatch_is_visible_instead_of_normal_state(study):
    from src.experiment_search import run_search, smoke
    from src.search_analysis import workflow_state

    config = study()
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    raw = json.loads(config.validation_dataset.read_text())
    raw["catalog_ids"].append("another")
    config.validation_dataset.write_text(json.dumps(raw))
    assert workflow_state(config)["status"] == "mismatch"


def test_three_parameter_sensitivity_holds_other_parameters_fixed(study):
    from src.experiment_search import run_search, smoke
    from src.search_analysis import parameter_sensitivity, plot_sensitivity

    config = study(
        grid="reverse=[false,true]\noffset=[0,1]\nunused=[1,2]", stage="B", budget=20, seeds="[0]"
    )
    smoke(config, factory=fixture_factory)
    run_search(config, factory=fixture_factory)
    frame = parameter_sensitivity(config)
    assert set(frame.varied_parameter) == {"reverse", "offset", "unused"}
    for name, group in frame.groupby("varied_parameter"):
        assert len(group) == 2
        assert all(name not in fixed for fixed in group.fixed_parameters)
        assert all(fixed == group.fixed_parameters.iloc[0] for fixed in group.fixed_parameters)
    fig = plot_sensitivity(frame, metric="ndcg", k=10)
    assert len(fig.axes) == 3
    assert all(ax.lines for ax in fig.axes)


def test_beyond_accuracy_averages_users_not_duplicate_requests(study):
    from dataclasses import replace

    from src.data import load_dataset
    from src.evaluation.benchmark import run_benchmark
    from src.search_metrics import ranking_metrics

    config = study()
    data = load_dataset(config.validation_dataset, top_k=20)
    duplicated = replace(
        data,
        requests=(*data.requests, replace(data.requests[0], request_id="duplicate")),
        ground_truth=(*data.ground_truth, replace(data.ground_truth[0], request_id="duplicate")),
    )
    model = fixture_factory("fixture", data.train_interactions, parameters={})
    features = {
        item: {"x": index + 1, "y": (index % 2) + 1} for index, item in enumerate(data.catalog_ids)
    }

    def measure(dataset):
        report = run_benchmark(dataset, model.recommend, model_name="fixture", seed=0)
        return ranking_metrics(dataset, report, features=features)["metrics"]

    for before, after in zip(measure(data), measure(duplicated), strict=True):
        assert before["novelty"] == pytest.approx(after["novelty"])
        assert (
            before["ild"] == pytest.approx(after["ild"])
            if before["ild"] is not None
            else after["ild"] is None
        )
