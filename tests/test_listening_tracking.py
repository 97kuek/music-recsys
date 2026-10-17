import json

from listening_support import write_bundle


def test_v2_mlflow_sends_only_aggregates_and_marks_model_failures(tmp_path):
    from mlflow import MlflowClient

    from src.domain.evaluation.listening import ListeningConfig
    from src.infrastructure.datasets.listening_data import load_listening_dataset
    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark
    from src.infrastructure.evaluation.tracking import TrackingConfig

    config = ListeningConfig(window_days=(90,))
    data = load_listening_dataset(write_bundle(tmp_path / "bundle"), config)
    tracking = TrackingConfig(
        enabled=True,
        tracking_uri=f"sqlite:///{tmp_path / 'tracking.db'}",
        experiment_name="listening",
    )

    def broken(request):
        raise RuntimeError("private")

    report = run_listening_benchmark(
        data, broken, config=config, model_name="broken", tracking=tracking
    )
    assert report.tracking.status == "FAILED"
    client = MlflowClient(tracking_uri=tracking.tracking_uri)
    run = client.get_run(report.tracking.run_id)
    assert run.info.status == "FAILED"
    assert run.data.metrics["n_generation_calls"] == 4
    path = client.download_artifacts(run.info.run_id, "reports/report.json", str(tmp_path))
    raw = json.loads(open(path).read())
    assert "runs" not in raw
    assert '"user_id"' not in json.dumps(raw)
