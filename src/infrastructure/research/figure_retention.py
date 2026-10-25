"""Explicit pruning of redrawable figures without rewriting numerical experiment evidence."""

import fcntl
import json
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.store import atomic_json

FORMAT = "music-recsys-figure-retention-v1"
PLAN = "music-recsys-figure-retention-plan-v1"
SUPPORTED = {
    "binary_ndcg",
    "recall",
    "graded_ndcg_new",
    "repeat_recall",
    "graded_ndcg_repeat",
    "ild",
    "coverage",
    "novelty",
    "latency",
    "play_count",
    "active_days",
    "binary_ndcg_heatmap",
    "recall_heatmap",
    "graded_ndcg_new_heatmap",
    "repeat_recall_heatmap",
    "graded_ndcg_repeat_heatmap",
}


def _redrawable(result, stem):
    accuracy = {
        "binary_ndcg": ("discovery", "binary_ndcg"),
        "recall": ("discovery", "recall"),
        "graded_ndcg_new": ("retention_proxy", "graded_ndcg"),
        "repeat_recall": ("repeat", "repeat_recall"),
        "graded_ndcg_repeat": ("repeat", "graded_ndcg"),
    }
    metric = stem.removesuffix("_heatmap")
    if metric in accuracy:
        task, name = accuracy[metric]
        return any(
            r["task"] == task
            and r["metric"] == name
            and r.get("mean") is not None
            and (
                stem.endswith("_heatmap")
                or r["window_days"] == result["primary"]["primary_window_days"]
            )
            and (
                name != "graded_ndcg" or r["day_weight"] == result["primary"]["primary_day_weight"]
            )
            for r in result["accuracy"]
        )
    if stem in {"ild", "coverage", "novelty"}:
        metric = "catalog_coverage" if stem == "coverage" else stem
        return any(r.get(metric) is not None for r in result["diversity"])
    if stem == "latency":
        return result["efficiency"].get("latency_mean") is not None
    observation = {
        "play_count": "evaluation_play_count",
        "active_days": "evaluation_active_days",
    }.get(stem)
    return observation is not None and any(
        r["observation"] == observation and r["pairs"] > 0 for r in result["observations"]
    )


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _path(directory, name):
    if not isinstance(name, str) or Path(name).name != name or not name:
        raise ValueError("A local figure basename is required")
    path = directory / name
    if path.is_symlink() or not path.resolve().is_relative_to(directory):
        raise ValueError("Invalid or symbolic-link figure path")
    return path


def _evidence(directory, manifest, expected_sha256):
    manifest = Path(manifest)
    if manifest.is_symlink() or file_sha256(manifest) != expected_sha256:
        raise ValueError("Figure retention plan hash changed")
    plan = _read(manifest)
    if plan.get("format") != PLAN:
        raise ValueError("Invalid figure retention plan")
    condition = plan.get("conditions", {}).get(str(directory))
    if not condition or not condition.get("figures"):
        raise ValueError("Condition is absent from the figure retention plan")
    for name, reference in condition["sources"].items():
        path = _path(directory, name)
        if path.is_symlink() or file_sha256(path) != reference["sha256"]:
            raise ValueError("Figure source evidence changed")
    if set(condition["sources"]) != {"config.json", "result.json", "metrics.csv"}:
        raise ValueError("Figure restoration requires config, result and metrics evidence")
    result = _read(directory / "result.json")
    if result.get("format") != "music-recsys-full-result-v1" or result.get("status") != "succeeded":
        raise ValueError("Only succeeded individual full results may have figures pruned")
    if not {"accuracy", "diversity", "efficiency", "observations", "primary"} <= result.keys():
        raise ValueError("Saved numerical data are insufficient to redraw every figure")
    for name, metadata in condition["figures"].items():
        path = _path(directory, name)
        if (
            path.stem not in SUPPORTED
            or path.suffix not in {".png", ".pdf"}
            or name not in result.get("figures", [])
        ):
            raise ValueError("Only declared, redrawable individual figures may be pruned")
        if not _redrawable(result, path.stem):
            raise ValueError("Saved measured data cannot redraw a requested figure")
        digest = metadata.get("sha256", "")
        if (
            len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or type(metadata.get("bytes")) is not int
            or metadata["bytes"] <= 0
        ):
            raise ValueError("Invalid planned figure evidence")
    return condition


def retention_record(directory, *, allow_incomplete=False):
    directory = Path(directory).resolve()
    marker = directory / "figures.retention.json"
    if not marker.exists():
        return None
    if marker.is_symlink():
        raise ValueError("Symbolic-link retention records are not accepted")
    record = _read(marker)
    if (
        record.get("format") != FORMAT
        or record.get("status") not in {"pruning", "pruned"}
        or record.get("directory") != str(directory)
    ):
        raise ValueError("Invalid figure retention evidence")
    condition = _evidence(directory, record["manifest"], record["manifest_sha256"])
    if (
        record.get("removed_files") != condition["figures"]
        or record.get("sources") != condition["sources"]
        or not record.get("reason")
    ):
        raise ValueError("Figure retention file evidence changed")
    if record["status"] != "pruned" and not allow_incomplete:
        raise ValueError("Figure pruning is incomplete; resume the recorded operation")
    for name, metadata in record["removed_files"].items():
        path = _path(directory, name)
        if path.exists() and (
            record["status"] == "pruned" or file_sha256(path) != metadata["sha256"]
        ):
            raise ValueError("Figure retention state differs from its files")
    return record


def figure_references(directory, names):
    """Return explicit available/pruned references; unrecorded missing files remain corruption."""
    directory = Path(directory).resolve()
    record = retention_record(directory)
    references = []
    for name in names:
        path = _path(directory, name)
        if path.is_file():
            references.append(dict(path=str(path), sha256=file_sha256(path), state="available"))
        elif record and name in record["removed_files"]:
            references.append(
                dict(
                    path=str(path),
                    sha256=record["removed_files"][name]["sha256"],
                    state="pruned",
                    retention=dict(
                        path=str(directory / "figures.retention.json"),
                        sha256=file_sha256(directory / "figures.retention.json"),
                    ),
                    recovery="Redraw to a new output with src.interfaces.cli.figure_redraw; saved result retains CI and observation counts",
                )
            )
        else:
            raise ValueError(f"Missing or invalid source figure without retention evidence: {path}")
    return references


def prune_figures(directory, *, manifest, expected_manifest_sha256, reason):
    """Record a verified external plan before deleting; resume only the identical operation."""
    directory = Path(directory).resolve()
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A figure retention reason is required")
    condition = _evidence(directory, manifest, expected_manifest_sha256)
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        condition = _evidence(directory, manifest, expected_manifest_sha256)
        record = retention_record(directory, allow_incomplete=True)
        if record is None:
            for name, metadata in condition["figures"].items():
                path = _path(directory, name)
                if (
                    not path.is_file()
                    or file_sha256(path) != metadata["sha256"]
                    or path.stat().st_size != metadata["bytes"]
                ):
                    raise ValueError("Figure changed before pruning")
            record = dict(
                format=FORMAT,
                status="pruning",
                directory=str(directory),
                manifest=str(Path(manifest).resolve()),
                manifest_sha256=expected_manifest_sha256,
                sources=condition["sources"],
                removed_files=condition["figures"],
                reason=reason,
                recovery="Saved result retains means, confidence intervals, diversity, efficiency and observation bins; redraw to a new output; image bytes/layout may differ",
            )
            atomic_json(directory / "figures.retention.json", record)
        elif record["manifest_sha256"] != expected_manifest_sha256 or record["reason"] != reason:
            raise ValueError("Figure pruning differs from the recorded operation")
        for name, metadata in record["removed_files"].items():
            path = _path(directory, name)
            if path.exists():
                if file_sha256(path) != metadata["sha256"]:
                    raise ValueError("Figure changed during pruning")
                path.unlink()
        record["status"] = "pruned"
        atomic_json(directory / "figures.retention.json", record)
        retention_record(directory)
    return record
