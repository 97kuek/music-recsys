"""new / repeatを各1回だけ生成し、保存ランキングからv2の全条件を評価する。"""

import csv
import hashlib
import io
import json
import platform
import tempfile
import time
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.domain.evaluation.listening_metrics import PreparedListeningResponse
from src.domain.recommendation.contracts import RecommendationResponse
from src.infrastructure.datasets.listening_data import file_sha256, read_user_observations
from src.infrastructure.evaluation.benchmark import _source_sha256
from src.infrastructure.evaluation.tracking import ExperimentRun, TrackingConfig, TrackingResult
from src.infrastructure.research.search_metrics import quantile
from src.infrastructure.research.usage import summarize_usage
from src.paths import PROJECT_ROOT


@dataclass(frozen=True)
class ListeningReport:
    schema_version: int
    metadata: dict
    summary: tuple[dict, ...]
    runs: tuple[dict, ...]
    tracking: TrackingResult | None = None
    beyond_accuracy: dict | None = None


def _csv(rows):
    stream = io.StringIO(newline="")
    fields = (
        "task",
        "window_days",
        "k",
        "day_weight",
        "metric",
        "value",
        "n_requests",
        "n_evaluated",
        "n_excluded",
        "n_failed",
    )
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def format_listening_summary(report):
    conditions = report.metadata["evaluation_conditions"]
    primary = conditions["primary"]
    rows = [
        r
        for r in report.summary
        if r["window_days"] == primary["window_days"]
        and r["k"] == primary["k"]
        and r["day_weight"] in (None, primary["day_weight"])
    ]
    lines = [
        f"# listening-v2: {report.metadata['model_name']}",
        "",
        f"Phase: {conditions['phase']}; window={primary['window_days']} days; K={primary['k']}; day_weight={primary['day_weight']}",
        "",
    ]
    for row in rows:
        value = (
            "unmeasured/no relevant candidates" if row["value"] is None else f"{row['value']:.6f}"
        )
        lines.append(
            f"- {row['task']} / {row['metric']}: {value} (evaluated {row['n_evaluated']}/{row['n_requests']})"
        )
    lines.append(
        f"- Generation calls: {report.metadata['n_generation_calls']} (K / windows / weights share rankings)"
    )
    return "\n".join(lines) + "\n"


def run_listening_benchmark(
    dataset,
    recommend,
    *,
    config,
    model_name,
    seed=0,
    training_seconds=None,
    config_sha256=None,
    model_parameters=None,
    tracking=None,
    features=None,
):
    import duckdb

    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be non-negative")
    if not set(config.window_days).issubset(dataset.manifest["window_days"]):
        raise ValueError("evaluation windows are not available")
    if (
        file_sha256(dataset.observations_path)
        != dataset.manifest["files"]["observations"]["sha256"]
    ):
        raise ValueError("observations changed after loading")
    tracking = tracking if tracking is not None else TrackingConfig.from_settings()
    from src.domain.recommendation.features import validate_features

    if features is not None:
        validate_features(features)
    manifest = dataset.manifest
    conditions = {
        "protocol": "listening-v2",
        "features_sha256": hashlib.sha256(
            json.dumps(features, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        if features is not None
        else None,
        "beyond_accuracy_version": "listening-diversity-v1",
        "schema_version": 2,
        "phase": manifest["phase"],
        "dataset_sha256": dataset.sha256,
        "learning_sha256": manifest["files"]["learning"]["sha256"],
        "observations_sha256": manifest["files"]["observations"]["sha256"],
        "source": {
            key: manifest.get("source", {}).get(key)
            for key in ("snapshot_id", "split_id", "dataset_version_id")
        },
        "source_files": manifest.get("source_files"),
        "prediction_at": manifest["prediction_at"],
        "window_ends": {
            str(w): (
                datetime.fromisoformat(manifest["prediction_at"]) + timedelta(days=w)
            ).isoformat()
            for w in config.window_days
        },
        "temporal": manifest["temporal"],
        "window_days": list(config.window_days),
        "ks": list(config.ks),
        "day_weights": list(config.day_weights),
        "primary": {
            "window_days": config.primary_window_days,
            "k": config.primary_k,
            "day_weight": config.primary_day_weight,
        },
        "candidate_policy": "new:learning_catalog-history; repeat:learning_catalog intersect history",
        "gain": "log2(1+n)+day_weight*log2(1+d); no exponential transform",
        "aggregation": "equal user mean within task/window/K/weight; exclude no positive candidates",
        "ranking_policy": "first 20 positions fixed; invalid/duplicate gain zero; no shift",
        "failure_policy": "zero if positive candidates exist, otherwise null",
        "timestamp_interpretation": "source_naive_timezone_unverified",
    }
    lock = PROJECT_ROOT / "uv.lock"
    metadata = {
        "dataset_id": dataset.dataset_id,
        "model_name": model_name,
        "seed": seed,
        "model_parameters": model_parameters or {},
        "training_seconds": training_seconds,
        "source_sha256": _source_sha256(),
        "dependency_lock_sha256": file_sha256(lock),
        "config_sha256": config_sha256,
        "python_version": platform.python_version(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_conditions": conditions,
    }
    experiment = (
        ExperimentRun(
            tracking,
            params={
                "protocol": "listening-v2",
                "dataset_sha256": dataset.sha256,
                "model_name": model_name,
                "parameters": model_parameters or {},
                "seed": seed,
                "conditions": json.dumps(conditions, sort_keys=True),
            },
        )
        if tracking.enabled
        else None
    )
    with experiment if experiment else nullcontext() as active:
        grouped, runs, durations = defaultdict(list), [], []
        generated_responses = []
        generation_calls = empty_candidates = 0
        with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as connection:
            connection.read_parquet(str(dataset.observations_path)).create_view("observations")
            current_user, observations = None, ()
            for request in dataset.requests:
                if request.user_id != current_user:
                    observations = read_user_observations(connection, request.user_id)
                    current_user = request.user_id
                duration = None
                if not request.candidate_ids:
                    empty_candidates += 1
                    response = RecommendationResponse(request.request_id, ())
                else:
                    generation_calls += 1
                    started = time.perf_counter()
                    try:
                        response = recommend(request)
                        if not isinstance(response, RecommendationResponse):
                            raise TypeError("invalid recommendation response")
                        if response.request_id != request.request_id:
                            raise ValueError("request_id mismatch")
                    except Exception as error:
                        response = RecommendationResponse(
                            request.request_id, (), status="error", error_type=type(error).__name__
                        )
                    duration = time.perf_counter() - started
                    durations.append(duration)
                    generated_responses.append(response)
                prepared = PreparedListeningResponse(request, response, observations)
                metrics, diagnostics = [], []
                for window in config.window_days:
                    for k in config.ks:
                        for index, weight in enumerate(config.day_weights):
                            score = prepared.evaluate(
                                window_days=window,
                                k=k,
                                day_weight=weight,
                            )
                            if window == config.window_days[0] and index == 0:
                                diagnostics.append(
                                    {
                                        "k": k,
                                        **{
                                            key: getattr(score, key)
                                            for key in (
                                                "returned_count",
                                                "valid_count",
                                                "invalid_count",
                                                "duplicate_count",
                                                "missing_count",
                                                "overflow_count",
                                            )
                                        },
                                    }
                                )
                            values = []
                            if index == 0:
                                if request.task == "new":
                                    values.extend(
                                        [
                                            ("discovery", "binary_ndcg", score.binary_ndcg),
                                            ("discovery", "recall", score.recall),
                                        ]
                                    )
                                else:
                                    values.append(("repeat", "repeat_recall", score.recall))
                            values.append(
                                (
                                    "retention_proxy" if request.task == "new" else "repeat",
                                    "graded_ndcg",
                                    score.graded_ndcg,
                                )
                            )
                            for task, metric, value in values:
                                day_weight = weight if metric == "graded_ndcg" else None
                                row = {
                                    "task": task,
                                    "window_days": window,
                                    "k": k,
                                    "day_weight": day_weight,
                                    "metric": metric,
                                    "value": value,
                                    "relevant_count": score.relevant_count,
                                }
                                metrics.append(row)
                                grouped[(task, window, k, day_weight, metric)].append(
                                    (value, response.status == "error")
                                )
                runs.append(
                    {
                        "request": asdict(request),
                        "response": asdict(response),
                        "recommendation_seconds": duration,
                        "metrics": metrics,
                        "diagnostics": diagnostics,
                    }
                )
        summary = []
        for key, values in grouped.items():
            measured = [value for value, _ in values if value is not None]
            summary.append(
                {
                    **dict(
                        zip(("task", "window_days", "k", "day_weight", "metric"), key, strict=True)
                    ),
                    "value": sum(measured) / len(measured) if measured else None,
                    "n_requests": len(values),
                    "n_evaluated": len(measured),
                    "n_excluded": len(values) - len(measured),
                    "n_failed": sum(failed for _, failed in values),
                }
            )
        summary.sort(
            key=lambda row: (
                row["task"],
                row["window_days"],
                row["k"],
                row["day_weight"] if row["day_weight"] is not None else -1,
                row["metric"],
            )
        )
        metadata.update(
            n_generation_calls=generation_calls,
            n_empty_candidates=empty_candidates,
            n_failed=sum(r["response"]["status"] == "error" for r in runs),
            total_recommendation_seconds=sum(durations),
            latency_p50=quantile(durations, 0.5),
            latency_p95=quantile(durations, 0.95),
            usage=summarize_usage(generated_responses),
        )
        from src.domain.evaluation.listening_diversity import listening_diversity

        beyond = listening_diversity(dataset, runs, ks=config.ks, features=features)
        report = ListeningReport(2, metadata, tuple(summary), tuple(runs), beyond_accuracy=beyond)
        if active:
            active.log_metrics(metadata["usage"]["metrics"])
            active.log_params(
                {
                    "source_sha256": metadata["source_sha256"],
                    "dependency_lock_sha256": metadata["dependency_lock_sha256"],
                }
            )
            active.log_metrics(
                {
                    key: metadata[key]
                    for key in (
                        "n_generation_calls",
                        "n_empty_candidates",
                        "n_failed",
                        "total_recommendation_seconds",
                        "latency_p50",
                        "latency_p95",
                        "training_seconds",
                    )
                }
            )
            for row in beyond["metrics"]:
                active.log_metrics(
                    {
                        f"{row['task']}.{name}": row[name]
                        for name in ("ild", "novelty", "catalog_coverage")
                    },
                    step=row["k"],
                )
            for row in summary:
                suffix = "binary" if row["day_weight"] is None else f"weight_{row['day_weight']}"
                active.log_metrics(
                    {
                        f"{row['task']}.{row['metric']}.days_{row['window_days']}.{suffix}": row[
                            "value"
                        ]
                    },
                    step=row["k"],
                )
            if metadata["n_failed"]:
                active.mark_failed()
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                (target / "report.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "metadata": metadata,
                            "summary": summary,
                            "beyond_accuracy": {
                                "metrics": beyond["metrics"],
                                "definitions": beyond["definitions"],
                            },
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                )
                (target / "metrics.csv").write_text(_csv(summary))
                (target / "summary.md").write_text(format_listening_summary(report))
                for name in ("report.json", "metrics.csv", "summary.md"):
                    active.log_artifact(target / name)
    return ListeningReport(
        2,
        metadata,
        tuple(summary),
        tuple(runs),
        experiment.result if experiment else None,
        beyond_accuracy=beyond,
    )


def save_listening_report(report, output_root, *, tracking=None):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="listening-", dir=output_root))
    try:
        (target / "results.json").write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        (target / "metrics.csv").write_text(_csv(report.summary))
        if report.beyond_accuracy and report.beyond_accuracy["metrics"]:
            rows = report.beyond_accuracy["metrics"]
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(
                {
                    key: json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
                    for key, value in row.items()
                }
                for row in rows
            )
            (target / "beyond-accuracy.csv").write_text(stream.getvalue())
        (target / "summary.md").write_text(format_listening_summary(report))
        if report.tracking:
            if tracking is None or not tracking.enabled:
                raise ValueError("tracking configuration is needed to link details")
            from mlflow import MlflowClient

            manifest = {
                "schema_version": 2,
                "relative_path": f"{target.name}/results.json",
                "sha256": file_sha256(target / "results.json"),
                "run_id": report.tracking.run_id,
            }
            (target / "details.json").write_text(json.dumps(manifest) + "\n")
            MlflowClient(tracking_uri=tracking.tracking_uri).log_artifact(
                report.tracking.run_id, str(target / "details.json"), artifact_path="reports"
            )
    except Exception:
        if report.tracking and tracking:
            try:
                from mlflow import MlflowClient

                MlflowClient(tracking_uri=tracking.tracking_uri).set_terminated(
                    report.tracking.run_id, "FAILED"
                )
            except Exception:
                pass
        raise
    return target
