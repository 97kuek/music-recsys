"""共通Runnerの記録内容を実MLflowの一時SQLite backendで確認する。"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tracking(tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    from music_recsys.evaluation.tracking import TrackingConfig

    monkeypatch.chdir(tmp_path)
    return TrackingConfig(
        enabled=True,
        tracking_uri=f"sqlite:///{tmp_path / 'tracking.db'}",
        experiment_name="tracking-tests",
    )


def test_runner_records_conditions_metrics_and_aggregate_artifacts(tracking, tmp_path):
    from mlflow import MlflowClient

    from music_recsys.evaluation.benchmark import run_experiment, save_report

    report = run_experiment(ROOT / "configs/toy.toml", tracking=tracking)
    run = MlflowClient(tracking_uri=tracking.tracking_uri).get_run(report.tracking.run_id)
    assert run.info.status == "FINISHED"
    assert run.data.params["seed"] == "42"
    assert run.data.params["model_name"] == "popularity"
    assert run.data.params["top_k"] == "2"
    for name in (
        "dataset_sha256",
        "train_sha256",
        "split_sha256",
        "source_sha256",
        "config_sha256",
    ):
        assert len(run.data.params[name]) == 64
    assert run.data.metrics["ndcg_at_2"] == pytest.approx(0.561019236584229)
    assert run.data.metrics["recall_at_2"] == 0.625
    assert run.data.metrics["n_requests"] == 5
    assert run.data.metrics["n_failed"] == 0
    client = MlflowClient(tracking_uri=tracking.tracking_uri)
    files = {item.path for item in client.list_artifacts(run.info.run_id, "reports")}
    assert files == {"reports/report.json", "reports/summary.md"}
    path = Path(client.download_artifacts(run.info.run_id, "reports/report.json", str(tmp_path)))
    payload = json.loads(path.read_text())
    assert payload["summary"]["recall"] == 0.625
    assert "runs" not in payload
    assert "user_id" not in path.read_text()
    saved = save_report(report, tmp_path / "runs")
    assert json.loads((saved / "results.json").read_text())["runs"][0]["request"]["user_id"]


def test_failed_recommendations_mark_mlflow_run_failed(tracking):
    from mlflow import MlflowClient

    from music_recsys.data import load_dataset
    from music_recsys.evaluation.benchmark import run_benchmark

    def recommend(request):
        raise RuntimeError("private message")

    report = run_benchmark(
        load_dataset(ROOT / "examples/toy/dataset.json", top_k=2),
        recommend,
        model_name="failed",
        tracking=tracking,
    )
    run = MlflowClient(tracking_uri=tracking.tracking_uri).get_run(report.tracking.run_id)
    assert run.info.status == "FAILED"
    assert report.tracking.status == "FAILED"
    assert run.data.metrics["n_failed"] == 5
    assert "private message" not in str(run.data)


def test_common_run_records_cost_and_exception_status(tracking):
    from mlflow import MlflowClient

    from music_recsys.evaluation.tracking import ExperimentRun

    with pytest.raises(RuntimeError, match="private"):
        with ExperimentRun(tracking, params={"model_name": "future-agent"}) as run:
            run.log_metrics({"input_tokens": 10, "cost_usd": 0.002})
            raise RuntimeError("private exception body")
    recorded = MlflowClient(tracking_uri=tracking.tracking_uri).get_run(run.run_id)
    assert recorded.info.status == "FAILED"
    assert recorded.data.metrics["cost_usd"] == 0.002
    assert recorded.data.tags["error_type"] == "RuntimeError"
    assert "private exception" not in str(recorded.data)


def test_tracking_failure_is_explicit_and_redacts_exception(monkeypatch, tracking):
    from mlflow import MlflowClient

    from music_recsys.evaluation.benchmark import run_experiment
    from music_recsys.evaluation.tracking import TrackingError

    def fail(*args, **kwargs):
        raise RuntimeError("password=private")

    monkeypatch.setattr(MlflowClient, "log_batch", fail)
    with pytest.raises(TrackingError) as failure:
        run_experiment(ROOT / "configs/toy.toml", tracking=tracking)
    assert "private" not in str(failure.value)
    client = MlflowClient(tracking_uri=tracking.tracking_uri)
    experiment = client.get_experiment_by_name("tracking-tests")
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 1
    assert runs[0].info.status == "FAILED"


def test_config_supports_env_overrides_without_modifying_experiment(monkeypatch, tmp_path):
    from music_recsys.evaluation.benchmark import load_config

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[benchmark]\ndataset="data.json"\nmodel="popularity"\ntop_k=2\nseed=42\n'
        '[tracking]\nenabled=true\nexperiment_name="config-name"\n'
    )
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5050")
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "env-name")
    assert load_config(config_path).tracking.tracking_uri == "http://127.0.0.1:5050"
    assert load_config(config_path).tracking.experiment_name == "env-name"
    monkeypatch.setenv("MUSIC_RECSYS_TRACKING", "false")
    assert load_config(config_path).tracking.enabled is False


def test_disabled_tracking_preserves_runner_without_mlflow(monkeypatch):
    from music_recsys.evaluation.benchmark import run_experiment

    monkeypatch.delenv("MUSIC_RECSYS_TRACKING", raising=False)
    assert run_experiment(ROOT / "configs/toy.toml").tracking is None


@pytest.mark.parametrize("setting", ["bad", "1"])
def test_invalid_tracking_toggle_is_rejected(monkeypatch, setting):
    from music_recsys.evaluation.benchmark import load_config

    monkeypatch.setenv("MUSIC_RECSYS_TRACKING", setting)
    with pytest.raises(ValueError):
        load_config(ROOT / "configs/toy.toml")


def test_server_refuses_domain_database(monkeypatch):
    from music_recsys.evaluation.tracking_server import ServerConfig

    monkeypatch.setenv("POSTGRES_DB", "music_recsys")
    monkeypatch.setenv("POSTGRES_USER", "music_recsys")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-password")
    monkeypatch.setenv("MLFLOW_DATABASE", "music_recsys")
    with pytest.raises(ValueError, match="separate"):
        ServerConfig.from_env()


def test_server_password_is_not_in_process_arguments_or_config_repr(monkeypatch, tmp_path):
    pytest.importorskip("sqlalchemy")
    from music_recsys.evaluation.tracking_server import ServerConfig

    monkeypatch.setenv("POSTGRES_DB", "music_recsys")
    monkeypatch.setenv("POSTGRES_USER", "music_recsys")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-password")
    monkeypatch.setenv("MLFLOW_DATABASE", "music_recsys_mlflow")
    monkeypatch.setenv("MLFLOW_ARTIFACT_ROOT", str(tmp_path))
    config = ServerConfig.from_env()
    assert "test-password" not in repr(config)
    assert "test-password" not in " ".join(config.command())
    assert config.server_env()["MLFLOW_BACKEND_STORE_URI"].startswith("postgresql+psycopg://")
    assert config.artifact_root == tmp_path


def test_undefined_ranking_metrics_are_not_logged_as_zero(tracking):
    from dataclasses import replace

    from mlflow import MlflowClient

    from music_recsys.data import load_dataset
    from music_recsys.evaluation.benchmark import run_benchmark
    from music_recsys.recommenders.popularity import PopularityRecommender
    from music_recsys.types import GroundTruth

    dataset = load_dataset(ROOT / "examples/toy/dataset.json", top_k=2)
    dataset = replace(
        dataset,
        ground_truth=tuple(GroundTruth(request.request_id, ()) for request in dataset.requests),
    )
    report = run_benchmark(
        dataset,
        PopularityRecommender(dataset.train_interactions).recommend,
        model_name="popularity",
        tracking=tracking,
    )
    run = MlflowClient(tracking_uri=tracking.tracking_uri).get_run(report.tracking.run_id)
    assert "ndcg_at_2" not in run.data.metrics
    assert "recall_at_2" not in run.data.metrics
    assert run.data.metrics["n_evaluated"] == 0
    assert run.data.metrics["n_excluded"] == 5


def test_metrics_reject_non_finite_values_and_fail_run(tracking):
    from mlflow import MlflowClient

    from music_recsys.evaluation.tracking import ExperimentRun

    with pytest.raises(ValueError, match="finite"):
        with ExperimentRun(tracking) as run:
            run.log_metrics({"cost_usd": float("nan")})
    recorded = MlflowClient(tracking_uri=tracking.tracking_uri).get_run(run.run_id)
    assert recorded.info.status == "FAILED"
    assert "cost_usd" not in recorded.data.metrics


@pytest.mark.parametrize(
    "uri", ["http://name:secret@localhost:5000", "http://localhost?token=secret"]
)
def test_config_rejects_credentials_in_tracking_uri(uri):
    from music_recsys.evaluation.tracking import TrackingConfig

    with pytest.raises(ValueError) as failure:
        TrackingConfig(enabled=True, tracking_uri=uri)
    assert "secret" not in str(failure.value)


def test_invalid_metric_input_does_not_expose_value(tracking):
    from music_recsys.evaluation.tracking import ExperimentRun

    with pytest.raises(ValueError) as failure:
        with ExperimentRun(tracking) as run:
            run.log_metrics({"cost_usd": "private-input"})
    assert "private-input" not in str(failure.value)
