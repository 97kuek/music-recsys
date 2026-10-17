import csv
import json

import pytest
from listening_support import write_bundle

from src.domain.recommendation.contracts import RecommendationResponse


def inputs(tmp_path):
    from src.domain.evaluation.listening import ListeningConfig
    from src.infrastructure.datasets.listening_data import load_listening_dataset

    config = ListeningConfig(window_days=(90,), day_weights=(0.0, 0.5, 1.0, 2.0))
    return load_listening_dataset(write_bundle(tmp_path / "bundle"), config), config


def test_runner_generates_only_two_rankings_per_user_for_all_conditions(tmp_path):
    from src.infrastructure.evaluation.listening_benchmark import (
        run_listening_benchmark,
        save_listening_report,
    )

    data, config = inputs(tmp_path)
    calls = []

    def recommend(request):
        calls.append((request.user_id, request.task))
        return RecommendationResponse(request.request_id, request.candidate_ids[:20])

    report = run_listening_benchmark(data, recommend, config=config, model_name="fixed")
    assert calls == [("u", "new"), ("u", "repeat"), ("v", "new"), ("v", "repeat")]
    assert report.metadata["n_generation_calls"] == 4
    assert {r["k"] for r in report.summary} == set(range(2, 21))
    discovery = [
        r for r in report.summary if r["task"] == "discovery" and r["metric"] == "binary_ndcg"
    ]
    assert len(discovery) == 19
    assert all(
        r["day_weight"] is None
        and r["value"] == 1
        and r["n_evaluated"] == 1
        and r["n_excluded"] == 1
        for r in discovery
    )
    target = save_listening_report(report, tmp_path / "reports")
    saved = json.loads((target / "results.json").read_text())
    assert saved["schema_version"] == 2
    assert len(saved["runs"]) == 4
    with (target / "metrics.csv").open() as stream:
        assert len(list(csv.DictReader(stream))) == len(report.summary)


def test_empty_candidates_skip_generation_but_keep_users(tmp_path):
    from src.domain.evaluation.listening import ListeningConfig
    from src.infrastructure.datasets.listening_data import load_listening_dataset
    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark

    learning = [
        {"user_id": user, "item_id": "a", "play_count": 1, "active_days": 1} for user in ("u", "v")
    ]
    config = ListeningConfig(window_days=(90,))
    data = load_listening_dataset(
        write_bundle(tmp_path / "bundle", learning=learning, observations=[]), config
    )
    calls = []

    def recommend(request):
        calls.append(request.task)
        return RecommendationResponse(request.request_id, request.candidate_ids)

    report = run_listening_benchmark(data, recommend, config=config, model_name="fixed")
    assert calls == ["repeat", "repeat"]
    assert len(report.runs) == 4
    assert all(row["value"] is None for row in report.summary)
    assert report.metadata["n_empty_candidates"] == 2


def test_model_failures_are_zero_when_truth_exists_and_do_not_persist_exception_text(tmp_path):
    from dataclasses import asdict

    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark

    data, config = inputs(tmp_path)

    def broken(request):
        raise RuntimeError("private contents")

    report = run_listening_benchmark(data, broken, config=config, model_name="broken")
    assert all(r["value"] == 0 for r in report.summary if r["task"] == "discovery")
    assert all(r["value"] is None for r in report.summary if r["task"] == "repeat")
    assert "private contents" not in json.dumps(asdict(report))


def test_cli_configuration_explicitly_selects_v2_and_preserves_model_parameters(tmp_path):
    from src.infrastructure.evaluation.benchmark import run_experiment

    bundle = write_bundle(tmp_path / "bundle")
    config = tmp_path / "benchmark.toml"
    config.write_text(
        f'[benchmark]\nprotocol="listening-v2"\ndataset="{bundle}"\nmodel="itemcf"\ntop_k=20\nseed=42\n[benchmark.parameters]\nneighbors=2\n[evaluation]\nwindow_days=[90]\nday_weights=[0.0,1.0]\n'
    )
    report = run_experiment(config)
    assert report.schema_version == 2
    assert report.metadata["model_parameters"] == {"neighbors": 2}
    assert report.metadata["evaluation_conditions"]["protocol"] == "listening-v2"


def test_legacy_reader_rejects_listening_report_explicitly():
    from src.interfaces.notebook.experiment_analysis import load_reports

    with pytest.raises(ValueError, match="listening-v2"):
        load_reports(
            {"v2": {"schema_version": 2, "metadata": {}, "summary": []}}, dataset_id="x", top_k=20
        )


def test_runner_saves_invalid_position_diagnostics(tmp_path):
    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark

    data, config = inputs(tmp_path)

    def recommend(request):
        return RecommendationResponse(
            request.request_id, ("outside", "outside", *request.candidate_ids)
        )

    report = run_listening_benchmark(data, recommend, config=config, model_name="invalid")
    diagnostic = next(row for row in report.runs[0]["diagnostics"] if row["k"] == 2)
    assert diagnostic["invalid_count"] == 2
    assert diagnostic["duplicate_count"] == 1
    assert diagnostic["valid_count"] == 0


def test_results_record_window_end_dates_without_requiring_source_bundle(tmp_path):
    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark

    data, config = inputs(tmp_path)
    report = run_listening_benchmark(
        data,
        lambda r: RecommendationResponse(r.request_id, r.candidate_ids),
        config=config,
        model_name="fixed",
    )
    assert report.metadata["evaluation_conditions"]["window_ends"] == {"90": "2017-06-27T00:00:00"}


def test_evaluator_prepares_candidates_once_per_request_not_for_every_k_or_gain(
    tmp_path, monkeypatch
):
    import src.domain.evaluation.listening_metrics as metrics
    from src.domain.evaluation.listening import ListeningConfig
    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark

    data, _ = inputs(tmp_path)
    calls = []
    original = metrics.eligible_candidates

    def counted(request):
        calls.append(request.request_id)
        return original(request)

    monkeypatch.setattr(metrics, "eligible_candidates", counted)
    config = ListeningConfig(window_days=(90,), ks=(1, 10, 20), day_weights=(0.0, 1.0, 2.0))
    report = run_listening_benchmark(
        data,
        lambda r: RecommendationResponse(r.request_id, r.candidate_ids),
        config=config,
        model_name="fixture",
    )
    assert len(calls) == len(data.requests)
    assert all(r["value"] == 1 for r in report.summary if r["task"] == "discovery")
