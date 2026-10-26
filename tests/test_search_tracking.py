"""成功・失敗の各試行を実MLflowへ記録し、生の要求は送らない。"""

from dataclasses import replace

import pytest
from test_experiment_search import fixture_factory


def test_failed_trial_also_creates_failed_mlflow_run(study, tmp_path):
    pytest.importorskip("mlflow")
    from mlflow import MlflowClient

    from src.infrastructure.evaluation.tracking import TrackingConfig
    from src.infrastructure.research.search import run_search, smoke

    tracking = TrackingConfig(
        enabled=True,
        tracking_uri=f"sqlite:///{tmp_path / 'tracking.db'}",
        experiment_name="search-tests",
    )
    config = replace(study(), tracking=tracking)
    smoke(config, factory=fixture_factory)

    def broken(*args, **kwargs):
        raise RuntimeError("private failure contents")

    result = run_search(config, factory=broken)
    assert result["status"] == "failed"
    client = MlflowClient(tracking_uri=tracking.tracking_uri)
    experiment = client.get_experiment_by_name("search-tests")
    runs = client.search_runs([experiment.experiment_id])
    assert any(r.info.status == "FAILED" for r in runs)
    finished = next(r for r in runs if r.info.status == "FINISHED")
    assert "ndcg" in finished.data.metrics
    path = client.download_artifacts(finished.info.run_id, "reports/report.json", str(tmp_path))
    assert '"user_id"' not in open(path).read()


def test_storage_failure_marks_mlflow_trial_failed(study, tmp_path, monkeypatch):
    from mlflow import MlflowClient

    import src.infrastructure.research.search as search
    from src.infrastructure.evaluation.tracking import TrackingConfig
    from src.infrastructure.research.search import trials

    tracking = TrackingConfig(
        enabled=True,
        tracking_uri=f"sqlite:///{tmp_path / 'tracking.db'}",
        experiment_name="storage-failure",
    )
    config = replace(study(), tracking=tracking)

    def broken_save(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(trials, "save_report", broken_save)
    assert search.smoke(config, factory=fixture_factory)["status"] == "failed"
    client = MlflowClient(tracking_uri=tracking.tracking_uri)
    experiment = client.get_experiment_by_name(tracking.experiment_name)
    runs = client.search_runs([experiment.experiment_id])
    assert [r.info.status for r in runs] == ["FAILED"]
