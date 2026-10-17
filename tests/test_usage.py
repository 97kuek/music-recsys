import pytest

from src.domain.recommendation.contracts import RecommendationResponse


def test_usage_totals_are_measured_and_partial_fields_remain_unknown():
    from src.domain.recommendation.contracts import RecommendationUsage
    from src.infrastructure.research.usage import summarize_usage

    first = RecommendationResponse(
        "a",
        (),
        usage=RecommendationUsage(
            input_tokens=10, output_tokens=5, api_cost_usd=0.01, llm_calls=1, tool_calls=0
        ),
    )
    second = RecommendationResponse(
        "b",
        (),
        usage=RecommendationUsage(input_tokens=20, output_tokens=7, llm_calls=2, tool_calls=1),
    )
    summary = summarize_usage([first, second])
    assert summary["metrics"]["input_tokens"] == 30
    assert summary["metrics"]["output_tokens"] == 12
    assert summary["metrics"]["llm_calls"] == 3
    assert summary["metrics"]["tool_calls"] == 1
    assert summary["metrics"]["api_cost"] is None
    assert summary["measured_requests"]["api_cost"] == 1
    assert summary["metrics"]["peak_memory"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"input_tokens": -1},
        {"llm_calls": True},
        {"api_cost_usd": float("nan")},
        {"api_cost_usd": -1},
        {"tool_calls": 1.2},
    ],
)
def test_invalid_usage_facts_are_rejected(kwargs):
    from src.domain.recommendation.contracts import RecommendationUsage

    with pytest.raises(ValueError):
        RecommendationUsage(**kwargs)


def test_real_runner_search_metrics_and_mlflow_aggregate_usage(tmp_path):
    from pathlib import Path

    from mlflow import MlflowClient

    from src.domain.recommendation.contracts import RecommendationUsage
    from src.infrastructure.datasets.json_dataset import load_dataset
    from src.infrastructure.evaluation.benchmark import run_benchmark
    from src.infrastructure.evaluation.tracking import TrackingConfig
    from src.infrastructure.research.search_metrics import ranking_metrics

    dataset = load_dataset(
        Path(__file__).resolve().parents[1] / "tests/fixtures/examples/toy/dataset.json", top_k=20
    )

    def recommend(request):
        return RecommendationResponse(
            request.request_id,
            request.candidate_ids[:20],
            usage=RecommendationUsage(
                input_tokens=10,
                output_tokens=2,
                api_cost_usd=0.001,
                llm_calls=1,
                tool_calls=0,
                peak_memory_bytes=100,
            ),
        )

    tracking = TrackingConfig(
        enabled=True, tracking_uri=f"sqlite:///{tmp_path / 'tracking.db'}", experiment_name="usage"
    )
    report = run_benchmark(dataset, recommend, model_name="instrumented-test", tracking=tracking)
    curves = ranking_metrics(dataset, report)
    assert curves["efficiency"]["input_tokens"] == 50
    assert curves["efficiency"]["api_cost"] == pytest.approx(0.005)
    assert curves["efficiency"]["peak_memory"] == 100
    assert (
        MlflowClient(tracking_uri=tracking.tracking_uri)
        .get_run(report.tracking.run_id)
        .data.metrics["input_tokens"]
        == 50
    )


def test_listening_runner_measures_generation_once_across_scoring_conditions(tmp_path):
    from listening_support import write_bundle

    from src.domain.evaluation.listening import ListeningConfig
    from src.domain.recommendation.contracts import RecommendationUsage
    from src.infrastructure.datasets.listening_data import load_listening_dataset
    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark

    config = ListeningConfig(window_days=(90,), day_weights=(0.0, 1.0, 2.0))
    data = load_listening_dataset(write_bundle(tmp_path / "bundle"), config)

    def recommend(request):
        return RecommendationResponse(
            request.request_id,
            request.candidate_ids,
            usage=RecommendationUsage(input_tokens=10, llm_calls=1),
        )

    report = run_listening_benchmark(data, recommend, config=config, model_name="instrumented-test")
    assert report.metadata["usage"]["metrics"]["input_tokens"] == 40
    assert report.metadata["usage"]["metrics"]["llm_calls"] == 4
