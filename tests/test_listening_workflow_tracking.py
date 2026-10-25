from dataclasses import replace
from pathlib import Path


def test_v2_grid_records_real_mlflow_runs_and_reuses_success(listening_study, tmp_path):
    from mlflow import MlflowClient

    from src.infrastructure.evaluation.tracking import TrackingConfig
    from src.infrastructure.research.search import run_search, smoke

    tracking = TrackingConfig(
        enabled=True,
        tracking_uri=f"sqlite:///{tmp_path / 'tracking.db'}",
        experiment_name="listening-workflow-tests",
    )
    config = replace(listening_study(), grid={"neighbors": [1]}, tracking=tracking)
    assert smoke(config)["status"] == "succeeded"
    assert run_search(config)["status"] == "succeeded"
    assert run_search(config)["reused_trials"] == 2
    client = MlflowClient(tracking_uri=tracking.tracking_uri)
    experiment = client.get_experiment_by_name(tracking.experiment_name)
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 3
    assert all(r.info.status == "FINISHED" for r in runs)
    validation = next(r for r in runs if r.data.params["phase"] == "validation")
    assert "graded_ndcg" in validation.data.metrics
    assert "training_seconds" in validation.data.metrics
    assert "trial_seconds" in validation.data.metrics
    artifact = client.download_artifacts(
        validation.info.run_id, "reports/report.json", str(tmp_path)
    )
    assert '"user_id"' not in Path(artifact).read_text()

    failed = replace(config, output=config.output.parent / "failed")
    assert smoke(failed)["status"] == "succeeded"

    def broken(*args, **kwargs):
        raise RuntimeError("private error contents")

    assert run_search(failed, factory=broken)["status"] == "failed"
    runs = client.search_runs([experiment.experiment_id])
    assert sum(r.info.status == "FAILED" for r in runs) == 2
