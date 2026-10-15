"""保存済み評価結果の条件照合・統計分析。モデルや評価器は実行しない。"""

import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, stdev
from tempfile import TemporaryDirectory
from typing import Any

Report = dict[str, Any]
Reports = Mapping[str, Report]
METRICS = ("ndcg", "recall")


@dataclass(frozen=True)
class BootstrapConfig:
    confidence: float = 0.95
    resamples: int = 2000
    seed: int = 42

    def __post_init__(self):
        if not 0 < self.confidence < 1:
            raise ValueError("confidence must be between 0 and 1")
        if type(self.resamples) is not int or self.resamples < 100:
            raise ValueError("resamples must be an integer >= 100")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")


def _number(value: Any, name: str, *, unit_interval: bool = False) -> None:
    if value is None:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"invalid {name}")
    if unit_interval and value > 1:
        raise ValueError(f"invalid {name}")


def _read(path: Path) -> Report:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validate(raw: Report) -> None:
    if raw.get("schema_version") == 2:
        raise ValueError(
            "listening-v2 requires separate condition-keyed CSV analysis; do not mix with v1 reports"
        )
    metadata, summary = raw["metadata"], raw["summary"]
    conditions = metadata.get("evaluation_conditions")
    required = {
        "protocol",
        "aggregation",
        "failure_policy",
        "evaluator_sha256",
        "input_sha256",
        "train_sha256",
        "split_sha256",
        "candidate_policy",
        "relevance",
    }
    if not isinstance(conditions, dict) or any(not conditions.get(key) for key in required):
        raise ValueError("missing evaluation_conditions; regenerate with the current CLI")
    for key in ("dataset_id", "dataset_sha256", "model_name"):
        if not isinstance(metadata.get(key), str) or not metadata[key]:
            raise ValueError(f"missing {key}")
    if type(metadata.get("top_k")) is not int or metadata["top_k"] < 1:
        raise ValueError("invalid top_k")
    for metric in METRICS:
        _number(summary[metric], metric, unit_interval=True)
    for key in ("n_requests", "n_evaluated", "n_excluded", "n_failed", "n_short"):
        if type(summary[key]) is not int or summary[key] < 0:
            raise ValueError(f"invalid {key}")
    if summary["n_requests"] != summary["n_evaluated"] + summary["n_excluded"]:
        raise ValueError("inconsistent evaluation counts")
    for metric in METRICS:
        if (summary[metric] is None) != (summary["n_evaluated"] == 0):
            raise ValueError("aggregate metric availability differs from n_evaluated")
    if any(summary[key] > summary["n_requests"] for key in ("n_failed", "n_short")):
        raise ValueError("aggregate counts exceed n_requests")
    for key in ("mean_recommendation_seconds", "total_recommendation_seconds"):
        _number(summary[key], key)
    _number(metadata["training_seconds"], "training_seconds")
    if "runs" not in raw:
        return
    runs = raw["runs"]
    if not isinstance(runs, list) or len(runs) != summary["n_requests"]:
        raise ValueError("request count differs from summary")
    ids = set()
    for run in runs:
        request, evaluation, response = run["request"], run["evaluation"], run["response"]
        request_id = request["request_id"]
        if not request_id or request_id in ids:
            raise ValueError("duplicate or empty request_id")
        ids.add(request_id)
        if request_id != evaluation["request_id"] or request_id != response["request_id"]:
            raise ValueError("request_id mismatch")
        if not request["user_id"] or request["top_k"] != metadata["top_k"]:
            raise ValueError("invalid request user_id or top_k")
        if response["status"] not in {"ok", "error"}:
            raise ValueError("invalid response status")
        for metric in METRICS:
            value = evaluation[metric]
            _number(value, metric, unit_interval=True)
            if (value is None) != (evaluation["relevant_count"] == 0):
                raise ValueError("metric availability differs from relevant_count")
            if response["status"] == "error" and value not in (None, 0):
                raise ValueError("failed request must retain a zero score")
        if run["recommendation_seconds"] is None:
            raise ValueError("missing recommendation_seconds")
        _number(run["recommendation_seconds"], "recommendation_seconds")
        _number(run.get("inference_cost"), "inference_cost")
    for metric in METRICS:
        values = [r["evaluation"][metric] for r in runs if r["evaluation"][metric] is not None]
        if len(values) != summary["n_evaluated"]:
            raise ValueError("evaluated request count differs from summary")
        mean = fmean(values) if values else None
        if mean != summary[metric] and (
            mean is None or summary[metric] is None or not math.isclose(mean, summary[metric])
        ):
            raise ValueError(f"{metric} differs from saved request mean")
    if sum(r["response"]["status"] == "error" for r in runs) != summary["n_failed"]:
        raise ValueError("failure count differs from summary")


def load_reports(
    sources: Mapping[str, Path | Report], *, dataset_id: str, top_k: int
) -> dict[str, Report]:
    """ローカル結果・取得済みMLflow集計を読み、同一条件のみ受け付ける。"""
    if not sources:
        raise ValueError("select at least one saved report")
    reports = {}
    conditions = None
    request_context = None
    for label, source in sources.items():
        raw = _read(source) if isinstance(source, (Path, str)) else source
        _validate(raw)
        metadata = raw["metadata"]
        if metadata["dataset_id"] != dataset_id:
            raise ValueError("different dataset_id; analyze benchmarks separately")
        if metadata["top_k"] != top_k:
            raise ValueError("different top_k")
        current = {
            key: metadata[key] for key in ("dataset_sha256", "top_k", "evaluation_conditions")
        }
        if conditions is not None:
            for key in current:
                if current[key] != conditions[key]:
                    raise ValueError(f"different {key}; comparison is not valid")
        conditions = current
        if "runs" in raw:
            context = {
                r["request"]["request_id"]: (r["request"], r["evaluation"]["relevant_count"])
                for r in raw["runs"]
            }
            if request_context is not None and context != request_context:
                raise ValueError("different request population or inputs")
            request_context = context
        reports[label] = raw
    # 集計のみのrunでも母集団の件数は一致させる。
    populations = {
        (r["summary"]["n_requests"], r["summary"]["n_evaluated"]) for r in reports.values()
    }
    if len(populations) != 1:
        raise ValueError("different request population counts")
    for metric in ("diversity", "coverage", "novelty"):
        definitions = set()
        for raw in reports.values():
            item = (raw.get("additional_metrics") or {}).get(metric)
            if item is not None and item.get("value") is not None:
                _number(item["value"], metric)
                if not item.get("definition"):
                    raise ValueError(f"missing {metric} definition")
                definitions.add(json.dumps(item["definition"], sort_keys=True))
        if len(definitions) > 1:
            raise ValueError(f"different {metric} definitions")
    return reports


def read_mlflow_report(
    run_id: str, *, tracking_uri: str, details_root: Path | None = None
) -> Report:
    """MLflowを読み取り、任意でmanifestに対応するローカル詳細を検証する。"""
    from mlflow import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    with TemporaryDirectory() as directory:
        aggregate = _read(Path(client.download_artifacts(run_id, "reports/report.json", directory)))
        if details_root is None:
            return aggregate
        manifest = _read(Path(client.download_artifacts(run_id, "reports/details.json", directory)))
        root = Path(details_root).resolve()
        path = (root / manifest["relative_path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("details path must stay within details_root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
            raise ValueError("details checksum mismatch")
        details = _read(path)
        if details.get("tracking", {}).get("run_id") != run_id:
            raise ValueError("details MLflow run_id mismatch")
        if any(details[key] != aggregate[key] for key in ("metadata", "summary")):
            raise ValueError("details differ from MLflow aggregate")
        return details


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def cluster_statistics(values: Sequence[tuple[str, float]], config: BootstrapConfig) -> dict:
    """要求平均を維持し、ユーザーの全要求をまとめて再標本化する。"""
    groups = defaultdict(list)
    for user, value in values:
        if not math.isfinite(value):
            raise ValueError("non-finite metric value")
        groups[user].append(value)
    clusters = [(sum(groups[user]), len(groups[user])) for user in sorted(groups)]
    means = [total / count for total, count in clusters]
    result = {
        "mean": fmean(value for _, value in values) if values else None,
        "n_evaluated": len(values),
        "n_users": len(clusters),
        "user_std": stdev(means) if len(means) > 1 else None,
        "user_q25": _quantile(means, 0.25),
        "user_median": _quantile(means, 0.5),
        "user_q75": _quantile(means, 0.75),
        "ci_low": None,
        "ci_high": None,
        "confidence": config.confidence,
        "resamples": config.resamples,
        "bootstrap_seed": config.seed,
        "ci_method": "user_cluster_percentile",
    }
    if len(clusters) >= 2:
        rng = random.Random(config.seed)
        sampled = []
        for _ in range(config.resamples):
            draw = rng.choices(clusters, k=len(clusters))
            sampled.append(sum(total for total, _ in draw) / sum(count for _, count in draw))
        tail = (1 - config.confidence) / 2
        result.update(ci_low=_quantile(sampled, tail), ci_high=_quantile(sampled, 1 - tail))
    return result


def _metric(metric: str) -> None:
    if metric not in METRICS:
        raise ValueError("select ndcg or recall")


def condition_rows(reports: Reports) -> list[dict]:
    return [
        {
            "run": label,
            **raw["metadata"],
            "details": "available" if "runs" in raw else "aggregate_only",
        }
        for label, raw in reports.items()
    ]


def performance_rows(
    reports: Reports, *, metric: str, bootstrap: BootstrapConfig = BootstrapConfig()
) -> list[dict]:
    _metric(metric)
    rows = []
    for label, raw in reports.items():
        values = [
            (r["request"]["user_id"], r["evaluation"][metric])
            for r in raw.get("runs", [])
            if r["evaluation"][metric] is not None
        ]
        stats = cluster_statistics(values, bootstrap)
        if "runs" not in raw:
            stats.update(
                mean=raw["summary"][metric], n_users=None, n_evaluated=raw["summary"]["n_evaluated"]
            )
        rows.append(
            {
                "run": label,
                "model": raw["metadata"]["model_name"],
                "seed": raw["metadata"]["seed"],
                "metric": metric,
                **stats,
                "n_excluded": raw["summary"]["n_excluded"],
                "details": "available" if "runs" in raw else "aggregate_only",
            }
        )
    return rows


def user_rows(reports: Reports, *, metric: str, baseline: str | None = None) -> list[dict]:
    _metric(metric)
    users = sorted(
        {r["request"]["user_id"] for raw in reports.values() for r in raw.get("runs", [])}
    )
    aliases = {user: f"user_{index + 1}" for index, user in enumerate(users)}
    rows = []
    for label, raw in reports.items():
        groups = defaultdict(list)
        for run in raw.get("runs", []):
            groups[run["request"]["user_id"]].append(run["evaluation"][metric])
        for user, all_values in groups.items():
            values = [value for value in all_values if value is not None]
            rows.append(
                {
                    "run": label,
                    "user": aliases[user],
                    "metric": metric,
                    "mean": fmean(values) if values else None,
                    "n_requests": len(all_values),
                    "n_evaluated": len(values),
                }
            )
    if baseline is not None:
        if baseline not in reports:
            raise ValueError("unknown baseline")
        reference = {row["user"]: row["mean"] for row in rows if row["run"] == baseline}
        for row in rows:
            base = reference.get(row["user"])
            row["delta_from_baseline"] = (
                row["mean"] - base if row["mean"] is not None and base is not None else None
            )
    return rows


def paired_differences(
    reports: Reports, *, baseline: str, metric: str, bootstrap: BootstrapConfig = BootstrapConfig()
) -> list[dict]:
    _metric(metric)
    if baseline not in reports:
        raise ValueError("unknown baseline")
    reference = reports[baseline]
    rows = []
    for label, raw in reports.items():
        if label == baseline:
            continue
        if "runs" not in reference or "runs" not in raw:
            rows.append(
                {
                    "run": label,
                    "baseline": baseline,
                    "mean": None,
                    "ci_low": None,
                    "ci_high": None,
                    "details": "aggregate_only",
                }
            )
            continue
        left = {r["request"]["request_id"]: r for r in reference["runs"]}
        right = {r["request"]["request_id"]: r for r in raw["runs"]}
        if left.keys() != right.keys():
            raise ValueError("different request population")
        values = []
        for key in sorted(left):
            a, b = left[key], right[key]
            if a["request"] != b["request"]:
                raise ValueError("different request inputs")
            x, y = a["evaluation"][metric], b["evaluation"][metric]
            if (x is None) != (y is None):
                raise ValueError("different evaluated requests")
            if x is not None:
                values.append((a["request"]["user_id"], y - x))
        rows.append(
            {
                "run": label,
                "baseline": baseline,
                **cluster_statistics(values, bootstrap),
                "details": "available",
            }
        )
    return rows


def group_rows(
    reports: Reports,
    *,
    metric: str,
    history_edges: tuple[int, ...] = (0, 1, 5, 20),
    attribute: str | None = None,
    bootstrap: BootstrapConfig = BootstrapConfig(),
) -> list[dict]:
    _metric(metric)
    if (
        not history_edges
        or history_edges[0] != 0
        or any(type(e) is not int for e in history_edges)
        or list(history_edges) != sorted(set(history_edges))
    ):
        raise ValueError("history_edges must be strictly increasing integers starting at 0")
    rows = []
    for label, raw in reports.items():
        groups = defaultdict(list)
        for run in raw.get("runs", []):
            if attribute is not None:
                value = (run.get("user_attributes") or {}).get(attribute)
                group = str(value) if value is not None else "(missing)"
            else:
                n = len(run["request"]["history"])
                index = max(i for i, edge in enumerate(history_edges) if n >= edge)
                upper = history_edges[index + 1] if index + 1 < len(history_edges) else "inf"
                group = f"[{history_edges[index]}, {upper})"
            groups[group].append(run)
        for group, runs in groups.items():
            values = [
                (r["request"]["user_id"], r["evaluation"][metric])
                for r in runs
                if r["evaluation"][metric] is not None
            ]
            rows.append(
                {
                    "run": label,
                    "group": group,
                    "n_requests": len(runs),
                    **cluster_statistics(values, bootstrap),
                }
            )
    return rows


def failure_rows(reports: Reports) -> list[dict]:
    rows = []
    for label, raw in reports.items():
        if "runs" not in raw:
            continue
        checks = {
            "error": lambda r: r["response"]["status"] == "error",
            "short_output": lambda r: r["evaluation"]["returned_count"] < raw["metadata"]["top_k"],
            "invalid_items": lambda r: r["evaluation"]["invalid_count"] > 0,
            "duplicate_items": lambda r: r["evaluation"]["duplicate_count"] > 0,
            "no_relevant_candidates": lambda r: r["evaluation"]["relevant_count"] == 0,
            "zero_recall": lambda r: r["evaluation"]["recall"] == 0,
        }
        for category, check in checks.items():
            count = sum(check(run) for run in raw["runs"])
            rows.append(
                {"run": label, "category": category, "count": count, "n_requests": len(raw["runs"])}
            )
        errors = defaultdict(int)
        for run in raw["runs"]:
            if run["response"]["status"] == "error":
                errors[run["response"].get("error_type") or "unknown"] += 1
        rows.extend(
            {
                "run": label,
                "category": f"error:{kind}",
                "count": count,
                "n_requests": len(raw["runs"]),
            }
            for kind, count in errors.items()
        )
    return rows


def additional_metric_rows(reports: Reports) -> list[dict]:
    return [
        {
            "run": label,
            "metric": metric,
            "value": ((raw.get("additional_metrics") or {}).get(metric) or {}).get("value"),
            "definition": ((raw.get("additional_metrics") or {}).get(metric) or {}).get(
                "definition"
            ),
            "status": "measured"
            if ((raw.get("additional_metrics") or {}).get(metric) or {}).get("value") is not None
            else "not_measured",
        }
        for label, raw in reports.items()
        for metric in ("diversity", "coverage", "novelty")
    ]


def efficiency_rows(reports: Reports) -> list[dict]:
    rows = []
    for label, raw in reports.items():
        runs = raw.get("runs", [])
        times = [r["recommendation_seconds"] for r in runs]
        costs = [r["inference_cost"] for r in runs if r.get("inference_cost") is not None]
        metadata = raw["metadata"]
        cost_complete = (
            bool(runs)
            and len(costs) == len(runs)
            and metadata.get("cost_currency")
            and metadata.get("cost_basis")
        )
        rows.append(
            {
                "run": label,
                "training_seconds": metadata["training_seconds"],
                "latency_mean": raw["summary"]["mean_recommendation_seconds"],
                "latency_median": _quantile(times, 0.5),
                "latency_p95": _quantile(times, 0.95),
                "n_requests": raw["summary"]["n_requests"],
                "cost_total": sum(costs) if cost_complete else None,
                "cost_per_request": fmean(costs) if cost_complete else None,
                "cost_measured_requests": len(costs),
                "cost_currency": metadata.get("cost_currency"),
                "cost_basis": metadata.get("cost_basis"),
                "environment": metadata["platform"],
                "measurement_conditions": metadata.get("measurement_conditions", "not_recorded"),
            }
        )
    return rows


def repetition_rows(reports: Reports, *, metric: str) -> list[dict]:
    """同じモデル設定の実行間のばらつき。ユーザーbootstrapとは集計しない。"""
    _metric(metric)
    groups = defaultdict(list)
    for label, raw in reports.items():
        metadata = raw["metadata"]
        parameters = metadata.get("model_parameters")
        key = (
            metadata["model_name"],
            metadata["source_sha256"],
            json.dumps(parameters, sort_keys=True),
            None if parameters is not None else label,
        )
        groups[key].append(raw)
    rows = []
    for (model, source, parameters, _), group in groups.items():
        values = [raw["summary"][metric] for raw in group if raw["summary"][metric] is not None]
        rows.append(
            {
                "model": model,
                "source_sha256": source,
                "model_parameters": parameters,
                "n_runs": len(group),
                "seeds": [raw["metadata"]["seed"] for raw in group],
                "n_seeds": len({raw["metadata"]["seed"] for raw in group}),
                "mean": fmean(values) if values else None,
                "run_std": stdev(values) if len(values) > 1 else None,
            }
        )
    return rows
