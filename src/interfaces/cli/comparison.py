"""Shared provenance checks and metric rows for saved experiment comparisons."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.research.results import read_result, verify_artifacts


@contextmanager
def staged_report(destination):
    """Publish report files only after rendering succeeds, with the result marker last."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="report-", dir=destination.parent) as temporary:
        output = Path(temporary) / "report"
        output.mkdir()
        yield output
        if not (output / "result.json").is_file():
            raise ValueError("Report has no result marker")
        destination.mkdir(parents=True, exist_ok=True)
        for path in output.iterdir():
            if path.name != "result.json":
                os.replace(path, destination / path.name)
        os.replace(output / "result.json", destination / "result.json")


def verified_result(trial, evaluation):
    result = read_result(trial)
    if result.get("status") != "succeeded" or result.get("phase") != "validation":
        raise ValueError("Comparison requires successful Validation")
    if trial.evaluation != evaluation:
        raise ValueError("Comparison evaluation conditions differ")
    saved = json.loads((trial.output / "config.json").read_text(encoding="utf-8"))
    identity = {key: saved[key] for key in ("manifest", "features_sha256", "population")}
    identity["evaluation_definitions"] = {
        key: value
        for key, value in result["definitions"].items()
        if key not in {"ranking", "latency"}
    }
    verify_artifacts(trial.output, result["artifacts"])
    source = dict(
        config=str(trial.path),
        result=str(trial.output / "result.json"),
        result_sha256=file_sha256(trial.output / "result.json"),
    )
    return result, saved, identity, source


def metric_rows(result, evaluation, *, efficiency=None):
    for score in result["accuracy"]:
        if score["window_days"] == evaluation.primary_window_days and score["day_weight"] in (
            None,
            evaluation.primary_day_weight,
        ):
            yield score
    for score in result["diversity"]:
        for metric in ("ild", "catalog_coverage", "novelty"):
            yield dict(task=score["task"], k=score["k"], metric=metric, mean=score[metric])
    for metric, value in result["efficiency"].items():
        if metric != "model" and (efficiency is None or metric in efficiency):
            yield dict(task="all", k=None, metric=metric, mean=value)
