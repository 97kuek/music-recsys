"""NotebookとCLIから共有する、要求実行・評価・結果保存。"""

import argparse
import hashlib
import json
import platform
import tempfile
import time
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import fmean

from music_recsys.data import BenchmarkDataset, load_dataset
from music_recsys.evaluation.metrics import evaluate_response
from music_recsys.evaluation.tracking import (
    ExperimentRun,
    TrackingConfig,
    TrackingError,
    TrackingResult,
)
from music_recsys.recommenders import create_recommender
from music_recsys.types import (
    EvaluationResult,
    RecommendationRequest,
    RecommendationResponse,
    validate_id,
    validate_top_k,
)


@dataclass(frozen=True)
class BenchmarkConfig:
    dataset_path: Path
    model: str
    top_k: int
    seed: int
    sha256: str
    tracking: TrackingConfig = TrackingConfig()


@dataclass(frozen=True)
class RunMetadata:
    dataset_id: str
    dataset_sha256: str
    model_name: str
    top_k: int
    seed: int
    started_at: str
    python_version: str
    package_version: str
    platform: str
    source_sha256: str
    config_sha256: str | None
    training_seconds: float
    evaluation_conditions: dict | None = None
    model_parameters: dict | None = None


@dataclass(frozen=True)
class RequestRun:
    request: RecommendationRequest
    response: RecommendationResponse
    evaluation: EvaluationResult
    recommendation_seconds: float


@dataclass(frozen=True)
class BenchmarkSummary:
    ndcg: float | None
    recall: float | None
    n_requests: int
    n_evaluated: int
    n_excluded: int
    n_failed: int
    n_short: int
    total_recommendation_seconds: float
    mean_recommendation_seconds: float


@dataclass(frozen=True)
class BenchmarkReport:
    metadata: RunMetadata
    summary: BenchmarkSummary
    runs: tuple[RequestRun, ...]
    tracking: TrackingResult | None = None


def load_config(path: Path) -> BenchmarkConfig:
    path = Path(path).resolve()
    content = path.read_bytes()
    raw = tomllib.loads(content.decode("utf-8"))
    try:
        config = raw["benchmark"]
        validate_id(config["dataset"])
        validate_top_k(config["top_k"])
        if type(config["seed"]) is not int or config["seed"] < 0:
            raise ValueError("seed must be a non-negative integer")
        if config["model"] != "popularity":
            raise ValueError("only the popularity model is currently supported by the CLI")
        return BenchmarkConfig(
            dataset_path=(path.parent / config["dataset"]).resolve(),
            model=config["model"],
            top_k=config["top_k"],
            seed=config["seed"],
            sha256=hashlib.sha256(content).hexdigest(),
            tracking=TrackingConfig.from_settings(raw.get("tracking")),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("invalid benchmark configuration") from error


def _source_sha256() -> str:
    root = Path(__file__).resolve().parents[1]
    checksum = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        checksum.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        checksum.update(path.read_bytes() + b"\0")
    return checksum.hexdigest()


def run_benchmark(
    dataset: BenchmarkDataset,
    recommend: Callable[[RecommendationRequest], RecommendationResponse],
    *,
    model_name: str,
    seed: int = 0,
    training_seconds: float = 0.0,
    config_sha256: str | None = None,
    model_parameters: dict | None = None,
    tracking: TrackingConfig | None = None,
) -> BenchmarkReport:
    """共通Runnerを実行し、有効な場合は集計成果物だけをMLflowへ記録する。"""
    tracking = tracking if tracking is not None else TrackingConfig.from_settings()
    if not tracking.enabled:
        return _run_benchmark(
            dataset,
            recommend,
            model_name=model_name,
            seed=seed,
            training_seconds=training_seconds,
            config_sha256=config_sha256,
            model_parameters=model_parameters,
        )
    conditions = {
        "dataset_id": dataset.dataset_id,
        "dataset_sha256": dataset.sha256,
        "train_sha256": _fingerprint(dataset.train_interactions),
        "split_sha256": _fingerprint((dataset.requests, dataset.ground_truth)),
        "split": "explicit_dataset_split",
        "model_name": model_name,
        "seed": seed,
        "config_sha256": config_sha256,
    }
    with ExperimentRun(tracking, params=conditions) as experiment:
        report = _run_benchmark(
            dataset,
            recommend,
            model_name=model_name,
            seed=seed,
            training_seconds=training_seconds,
            config_sha256=config_sha256,
            model_parameters=model_parameters,
        )
        metadata = asdict(report.metadata)
        experiment.log_params(
            {
                key: metadata[key]
                for key in (
                    "top_k",
                    "python_version",
                    "package_version",
                    "platform",
                    "source_sha256",
                )
            }
        )
        metrics = asdict(report.summary)
        metrics[f"ndcg_at_{report.metadata.top_k}"] = metrics.pop("ndcg")
        metrics[f"recall_at_{report.metadata.top_k}"] = metrics.pop("recall")
        metrics["training_seconds"] = training_seconds
        experiment.log_metrics(metrics)
        if report.summary.n_failed:
            experiment.mark_failed()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "report.json").write_text(
                json.dumps(
                    {"metadata": metadata, "summary": asdict(report.summary)},
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (path / "summary.md").write_text(format_summary(report), encoding="utf-8")
            experiment.log_artifact(path / "report.json")
            experiment.log_artifact(path / "summary.md")
    return replace(report, tracking=experiment.result)


def _fingerprint(value: object) -> str:
    def serialize(item: object) -> object:
        return asdict(item)

    return hashlib.sha256(
        json.dumps(value, default=serialize, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _run_benchmark(
    dataset: BenchmarkDataset,
    recommend: Callable[[RecommendationRequest], RecommendationResponse],
    *,
    model_name: str,
    seed: int,
    training_seconds: float,
    config_sha256: str | None,
    model_parameters: dict | None,
) -> BenchmarkReport:
    """正解を推薦関数へ渡さず、失敗した要求も結果に残す。"""
    validate_id(model_name)
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    top_ks = {request.top_k for request in dataset.requests}
    if len(top_ks) != 1:
        raise ValueError("all requests must use the same top_k")
    top_k = next(iter(top_ks))
    metadata = RunMetadata(
        dataset_id=dataset.dataset_id,
        dataset_sha256=dataset.sha256,
        model_name=model_name,
        top_k=top_k,
        seed=seed,
        started_at=datetime.now(timezone.utc).isoformat(),
        python_version=platform.python_version(),
        package_version=version("music-recsys"),
        platform=platform.platform(),
        source_sha256=_source_sha256(),
        config_sha256=config_sha256,
        training_seconds=training_seconds,
        evaluation_conditions={
            "protocol": "explicit-json-binary-unseen-v1",
            "snapshot_id": (dataset.provenance or {}).get("snapshot_id"),
            "dataset_provenance": dataset.provenance,
            "data_format": "explicit-json-v1",
            "train_sha256": _fingerprint(dataset.train_interactions),
            "split_sha256": _fingerprint((dataset.requests, dataset.ground_truth)),
            "input_sha256": _fingerprint(dataset.requests),
            "candidate_policy": "exclude_request_history",
            "relevance": "binary_within_candidates",
            "aggregation": "request_mean_excluding_no_relevant_candidates",
            "failure_policy": "zero_if_relevant_candidates_exist",
            "evaluator_sha256": hashlib.sha256(
                Path(__file__).with_name("metrics.py").read_bytes()
            ).hexdigest(),
        },
        model_parameters=model_parameters,
    )
    truth_by_id = {truth.request_id: truth for truth in dataset.ground_truth}
    runs = []
    for request in dataset.requests:
        start = time.perf_counter()
        try:
            response = recommend(request)
            duration = time.perf_counter() - start
            if not isinstance(response, RecommendationResponse):
                raise TypeError("recommend must return RecommendationResponse")
            evaluation = evaluate_response(request, response, truth_by_id[request.request_id])
        except Exception as error:
            duration = time.perf_counter() - start
            response = RecommendationResponse(
                request.request_id, (), status="error", error_type=type(error).__name__
            )
            evaluation = evaluate_response(request, response, truth_by_id[request.request_id])
        runs.append(RequestRun(request, response, evaluation, duration))
    ndcg_values = [run.evaluation.ndcg for run in runs if run.evaluation.ndcg is not None]
    recall_values = [run.evaluation.recall for run in runs if run.evaluation.recall is not None]
    durations = [run.recommendation_seconds for run in runs]
    summary = BenchmarkSummary(
        ndcg=fmean(ndcg_values) if ndcg_values else None,
        recall=fmean(recall_values) if recall_values else None,
        n_requests=len(runs),
        n_evaluated=len(ndcg_values),
        n_excluded=len(runs) - len(ndcg_values),
        n_failed=sum(run.response.status == "error" for run in runs),
        n_short=sum(run.evaluation.returned_count < top_k for run in runs),
        total_recommendation_seconds=sum(durations),
        mean_recommendation_seconds=fmean(durations),
    )
    return BenchmarkReport(metadata, summary, tuple(runs))


def run_experiment(config_path: Path, *, tracking: TrackingConfig | None = None) -> BenchmarkReport:
    """設定からデータを読み、Popularityを学習して共通Runnerを呼ぶ。"""
    config = load_config(config_path)
    dataset = load_dataset(config.dataset_path, top_k=config.top_k)
    start = time.perf_counter()
    model = create_recommender(config.model, dataset.train_interactions)
    training_seconds = time.perf_counter() - start
    return run_benchmark(
        dataset,
        model.recommend,
        model_name=config.model,
        seed=config.seed,
        training_seconds=training_seconds,
        config_sha256=config.sha256,
        model_parameters={},
        tracking=tracking if tracking is not None else config.tracking,
    )


def format_summary(report: BenchmarkReport) -> str:
    def metric(value: float | None) -> str:
        return "undefined" if value is None else f"{value:.6f}"

    metadata, summary = report.metadata, report.summary
    return "\n".join(
        [
            f"# Benchmark: {metadata.model_name}",
            "",
            f"Dataset: {metadata.dataset_id}",
            f"Started at (UTC): {metadata.started_at}",
            f"Seed: {metadata.seed}",
            "",
            f"- NDCG@{metadata.top_k}: {metric(summary.ndcg)}",
            f"- Recall@{metadata.top_k}: {metric(summary.recall)}",
            f"- Requests: {summary.n_requests}",
            f"- Evaluated: {summary.n_evaluated}",
            f"- Excluded (no relevant candidates): {summary.n_excluded}",
            f"- Failed: {summary.n_failed}",
            f"- Short outputs (including failures): {summary.n_short}",
            f"- Training seconds: {metadata.training_seconds:.6f}",
            f"- Mean recommendation seconds: {summary.mean_recommendation_seconds:.6f}",
            "",
            f"Dataset SHA-256: {metadata.dataset_sha256}",
            f"Source SHA-256: {metadata.source_sha256}",
            f"Config SHA-256: {metadata.config_sha256 or 'not supplied'}",
            "",
        ]
    )


def save_report(
    report: BenchmarkReport, output_root: Path, *, tracking: TrackingConfig | None = None
) -> Path:
    """新しい実行ディレクトリへ保存する。既存結果は上書きしない。"""
    serialized = json.dumps(asdict(report), ensure_ascii=False, indent=2, allow_nan=False)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-")
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    (directory / "results.json").write_text(serialized + "\n", encoding="utf-8")
    (directory / "summary.md").write_text(format_summary(report), encoding="utf-8")
    if tracking is not None and tracking.enabled:
        if report.tracking is None:
            raise ValueError("report has no MLflow run to link")
        manifest = directory / "details.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "relative_path": f"{directory.name}/results.json",
                    "sha256": hashlib.sha256((directory / "results.json").read_bytes()).hexdigest(),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            from mlflow import MlflowClient

            client = MlflowClient(tracking_uri=tracking.tracking_uri)
            client.log_artifact(report.tracking.run_id, str(manifest), artifact_path="reports")
        except Exception:
            raise TrackingError(
                "Failed to link saved details to MLflow; local report is retained"
            ) from None
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a shared music recommendation benchmark")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="parent directory for a new result directory")
    parser.add_argument("--tracking", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        tracking = config.tracking
        if args.tracking is not None:
            tracking = replace(tracking, enabled=args.tracking)
        report = run_experiment(args.config, tracking=tracking)
        print(format_summary(report))
        if report.tracking is not None:
            print(f"MLflow: {report.tracking.run_id} ({report.tracking.status})")
        if args.output is not None:
            print(f"Saved: {save_report(report, args.output, tracking=tracking)}")
    except (OSError, ValueError, TrackingError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
