"""保存結果の条件照合・ユーザー単位統計・欠測の扱いを検証する。"""

import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from src import experiment_analysis as analysis
from src.evaluation.benchmark import run_experiment
from src.evaluation.tracking import TrackingConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def saved(tmp_path):
    raw = asdict(
        run_experiment(
            ROOT / "tests/fixtures/configs/toy.toml", tracking=TrackingConfig(enabled=False)
        )
    )
    path = tmp_path / "results.json"
    path.write_text(json.dumps(raw))
    return path


def test_load_saved_results_without_running_models(saved):
    reports = analysis.load_reports({"baseline": saved}, dataset_id="toy-music-v1", top_k=2)
    rows = analysis.performance_rows(
        reports, metric="recall", bootstrap=analysis.BootstrapConfig(resamples=200)
    )
    assert rows[0]["mean"] == 0.625
    assert rows[0]["n_users"] == 4
    assert rows[0]["n_evaluated"] == 4
    assert rows[0]["ci_low"] <= 0.625 <= rows[0]["ci_high"]
    assert rows[0]["n_excluded"] == 1


@pytest.mark.parametrize("field", ["dataset_sha256", "top_k", "evaluation_conditions"])
def test_mismatched_conditions_cannot_be_compared(saved, tmp_path, field):
    other = json.loads(saved.read_text())
    if field == "top_k":
        other["metadata"][field] = 3
    elif field == "evaluation_conditions":
        other["metadata"][field]["failure_policy"] = "drop_failures"
    else:
        other["metadata"][field] = "different"
    path = tmp_path / "other.json"
    path.write_text(json.dumps(other))
    with pytest.raises(ValueError, match=field):
        analysis.load_reports({"a": saved, "b": path}, dataset_id="toy-music-v1", top_k=2)


def test_datasets_and_unknown_provenance_are_rejected(saved, tmp_path):
    with pytest.raises(ValueError, match="dataset_id"):
        analysis.load_reports({"a": saved}, dataset_id="talkplay-2026", top_k=2)
    raw = json.loads(saved.read_text())
    del raw["metadata"]["evaluation_conditions"]
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="evaluation_conditions"):
        analysis.load_reports({"a": path}, dataset_id="toy-music-v1", top_k=2)


def test_cluster_bootstrap_keeps_request_weighting_and_repeated_users_together():
    stats = analysis.cluster_statistics(
        [("u1", 0.0)] * 9 + [("u2", 1.0)], analysis.BootstrapConfig(resamples=1000)
    )
    assert stats["mean"] == pytest.approx(0.1)
    assert stats["n_users"] == 2
    assert stats["ci_low"] == 0.0
    assert stats["ci_high"] == 1.0
    assert stats == analysis.cluster_statistics(
        [("u1", 0.0)] * 9 + [("u2", 1.0)], analysis.BootstrapConfig(resamples=1000)
    )
    single = analysis.cluster_statistics([("u1", 0.0), ("u1", 1.0)], analysis.BootstrapConfig())
    assert single["mean"] == 0.5
    assert single["ci_low"] is None and single["user_std"] is None


def test_pairing_uses_request_ids_not_saved_order(saved, tmp_path):
    other = json.loads(saved.read_text())
    other["runs"].reverse()
    path = tmp_path / "reordered.json"
    path.write_text(json.dumps(other))
    reports = analysis.load_reports({"a": saved, "b": path}, dataset_id="toy-music-v1", top_k=2)
    row = analysis.paired_differences(reports, baseline="a", metric="recall")[0]
    assert row["mean"] == 0.0
    assert row["ci_low"] == row["ci_high"] == 0.0
    assert row["n_evaluated"] == 4
    assert len(analysis.user_rows(reports, metric="recall")) == 10


def test_missing_requests_or_duplicate_ids_cannot_silently_disappear(saved, tmp_path):
    raw = json.loads(saved.read_text())
    raw["runs"][1]["request"]["request_id"] = raw["runs"][0]["request"]["request_id"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="request"):
        analysis.load_reports({"a": path}, dataset_id="toy-music-v1", top_k=2)


def test_aggregate_report_has_no_invented_distribution_or_latency(saved, tmp_path):
    raw = json.loads(saved.read_text())
    del raw["runs"]
    path = tmp_path / "aggregate.json"
    path.write_text(json.dumps(raw))
    reports = analysis.load_reports({"a": path}, dataset_id="toy-music-v1", top_k=2)
    row = analysis.performance_rows(reports, metric="ndcg")[0]
    assert row["mean"] == pytest.approx(0.561019236584229)
    assert row["ci_low"] is None and row["n_users"] is None
    assert analysis.user_rows(reports, metric="ndcg") == []
    efficiency = analysis.efficiency_rows(reports)[0]
    assert efficiency["latency_p95"] is None and efficiency["cost_total"] is None
    assert all(row["value"] is None for row in analysis.additional_metric_rows(reports))


def test_history_groups_and_missing_attributes_are_explicit(saved):
    reports = analysis.load_reports({"a": saved}, dataset_id="toy-music-v1", top_k=2)
    rows = analysis.group_rows(reports, metric="recall", history_edges=(0, 1, 3))
    cold = next(row for row in rows if row["group"] == "[0, 1)")
    assert cold["n_requests"] == 2 and cold["n_evaluated"] == 1
    assert cold["mean"] == 0.0
    attributes = analysis.group_rows(reports, metric="recall", attribute="country")
    assert len(attributes) == 1 and attributes[0]["group"] == "(missing)"
    assert attributes[0]["n_requests"] == 5


def test_failure_and_undefined_metrics_are_not_confused(saved):
    reports = analysis.load_reports({"a": saved}, dataset_id="toy-music-v1", top_k=2)
    failures = {row["category"]: row["count"] for row in analysis.failure_rows(reports)}
    assert failures["no_relevant_candidates"] == 1
    assert failures["zero_recall"] == 1
    assert failures["error"] == 0


def test_cost_and_extra_metrics_require_definitions_and_complete_measurement(saved, tmp_path):
    raw = json.loads(saved.read_text())
    raw["metadata"].update(cost_currency="USD", cost_basis="test-pricing-v1")
    for run in raw["runs"]:
        run["inference_cost"] = 0.02
    raw["additional_metrics"] = {"coverage": {"value": 0.5, "definition": "catalog-v1"}}
    path = tmp_path / "cost.json"
    path.write_text(json.dumps(raw))
    reports = analysis.load_reports({"a": path}, dataset_id="toy-music-v1", top_k=2)
    assert analysis.efficiency_rows(reports)[0]["cost_total"] == pytest.approx(0.1)
    assert (
        next(r for r in analysis.additional_metric_rows(reports) if r["metric"] == "coverage")[
            "value"
        ]
        == 0.5
    )
    partial = copy.deepcopy(raw)
    del partial["runs"][0]["inference_cost"]
    path.write_text(json.dumps(partial))
    reports = analysis.load_reports({"a": path}, dataset_id="toy-music-v1", top_k=2)
    assert analysis.efficiency_rows(reports)[0]["cost_total"] is None
    assert analysis.efficiency_rows(reports)[0]["cost_measured_requests"] == 4


@pytest.mark.parametrize("field,value", [("n_failed", 999), ("n_short", 999), ("ndcg", None)])
def test_invalid_aggregate_is_rejected(saved, tmp_path, field, value):
    raw = json.loads(saved.read_text())
    del raw["runs"]
    raw["summary"][field] = value
    path = tmp_path / "bad-aggregate.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        analysis.load_reports({"a": path}, dataset_id="toy-music-v1", top_k=2)


def test_optional_metrics_can_be_null_but_required_timings_cannot(saved, tmp_path):
    raw = json.loads(saved.read_text())
    raw["additional_metrics"] = {"coverage": None}
    path = tmp_path / "nulls.json"
    path.write_text(json.dumps(raw))
    reports = analysis.load_reports({"a": path}, dataset_id="toy-music-v1", top_k=2)
    assert all(row["value"] is None for row in analysis.additional_metric_rows(reports))
    raw["runs"][0]["recommendation_seconds"] = None
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="recommendation_seconds"):
        analysis.load_reports({"a": path}, dataset_id="toy-music-v1", top_k=2)


def test_repetitions_do_not_pool_different_model_implementations(saved, tmp_path):
    raw = json.loads(saved.read_text())
    raw["metadata"]["source_sha256"] = "modified-model-source"
    path = tmp_path / "modified.json"
    path.write_text(json.dumps(raw))
    reports = analysis.load_reports({"a": saved, "b": path}, dataset_id="toy-music-v1", top_k=2)
    assert [row["n_runs"] for row in analysis.repetition_rows(reports, metric="recall")] == [1, 1]


def test_user_differences_and_paired_request_difference(saved, tmp_path):
    raw = json.loads(saved.read_text())
    raw["runs"][0]["evaluation"]["recall"] = 0.0
    raw["summary"]["recall"] = 0.5
    path = tmp_path / "lower-recall.json"
    path.write_text(json.dumps(raw))
    reports = analysis.load_reports({"a": saved, "b": path}, dataset_id="toy-music-v1", top_k=2)
    row = analysis.paired_differences(reports, baseline="a", metric="recall")[0]
    assert row["mean"] == -0.125
    users = analysis.user_rows(reports, metric="recall", baseline="a")
    assert sorted(
        r["delta_from_baseline"]
        for r in users
        if r["run"] == "b" and r["delta_from_baseline"] is not None
    ) == [-0.5, 0.0, 0.0, 0.0]


def test_additional_metric_definitions_must_match(saved, tmp_path):
    raw = json.loads(saved.read_text())
    raw["additional_metrics"] = {"novelty": {"value": 2.0, "definition": "train-popularity-v1"}}
    first = tmp_path / "first.json"
    first.write_text(json.dumps(raw))
    raw["additional_metrics"]["novelty"]["definition"] = "test-popularity-v1"
    second = tmp_path / "second.json"
    second.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="novelty"):
        analysis.load_reports({"a": first, "b": second}, dataset_id="toy-music-v1", top_k=2)


def test_absent_optional_sections_remain_unmeasured(saved, tmp_path):
    raw = json.loads(saved.read_text())
    raw["additional_metrics"] = None
    for run in raw["runs"]:
        run["user_attributes"] = None
    path = tmp_path / "optional-null.json"
    path.write_text(json.dumps(raw))
    reports = analysis.load_reports({"a": path}, dataset_id="toy-music-v1", top_k=2)
    assert all(row["value"] is None for row in analysis.additional_metric_rows(reports))
    assert (
        analysis.group_rows(reports, metric="recall", attribute="country")[0]["group"]
        == "(missing)"
    )
