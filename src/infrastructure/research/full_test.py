"""Check full Test training provenance without expanding histories in Python."""

import json
from dataclasses import asdict, replace
from pathlib import Path

from src.domain.evaluation.listening import ListeningConfig
from src.infrastructure.datasets.listening_data import read_bundle
from src.infrastructure.evaluation.tracking import TrackingConfig
from src.infrastructure.research.results import verify_artifacts
from src.infrastructure.research.search import StudyConfig


def validate_training(config, manifest, paths, counts):
    import duckdb

    before, previous = read_bundle(config.validation_dataset)
    if manifest["phase"] != "test" or before["phase"] != "validation":
        raise ValueError("Test/Validation phase mismatch")
    for key in ("dataset_id", "source", "temporal"):
        if manifest[key] != before[key]:
            raise ValueError("Test/Validation provenance mismatch")
    if set(manifest["source_files"]) != {"train", "validation", "test"}:
        raise ValueError("Test requires Train + Validation source evidence")
    for role in ("train", "validation"):
        if manifest["source_files"][role] != before["source_files"][role]:
            raise ValueError("Test/Validation source file mismatch")
    expected = sum(manifest["source_files"][r]["row_count"] for r in ("train", "validation"))
    if counts["events"] != expected:
        raise ValueError("Test training must include all Train + Validation events")
    with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as c:
        c.read_parquet(str(previous["learning"])).create_view("before_learning")
        c.read_parquet(str(previous["observations"])).create_view("before_observations")
        c.read_parquet(str(paths["learning"])).create_view("test_learning")
        missing = c.execute("""WITH increments AS (
            SELECT user_id,item_id,max(evaluation_play_count) plays,
                max(evaluation_active_days) n_days
            FROM before_observations GROUP BY user_id,item_id
        ), required AS (
            SELECT user_id,item_id,coalesce(play_count,0)+coalesce(plays,0) plays,
                coalesce(active_days,0)+coalesce(n_days,0) n_days
            FROM before_learning FULL JOIN increments USING(user_id,item_id)
        ) SELECT count(*) FROM required r LEFT JOIN test_learning t USING(user_id,item_id)
        WHERE t.user_id IS NULL OR t.play_count<r.plays OR t.active_days<r.n_days""").fetchone()[0]
        if missing:
            raise ValueError("Test training omits Train + Validation listening history")
    return {"training_events": expected, "training_periods": ["train", "validation"]}


def restore(directory):
    """Restore the exact recorded settings, including the original config path."""
    raw = json.loads((Path(directory) / "config.json").read_text(encoding="utf-8"))["settings"]
    for name in ("path", "validation_dataset", "test_dataset", "output", "parent", "features"):
        raw[name] = Path(raw[name]) if raw[name] is not None else None
    raw["seeds"] = tuple(raw["seeds"])
    raw["tracking"] = TrackingConfig(**raw["tracking"])
    options = raw["evaluation"]
    for name in ("ks", "window_days", "day_weights"):
        options[name] = tuple(options[name])
    raw["evaluation"] = ListeningConfig(**options)
    config = StudyConfig(**raw)
    if config.output.resolve() != Path(directory).resolve() or config.protocol != "listening-v2":
        raise ValueError("Saved settings do not identify the Validation output")
    return config


def verify_test(config, protocol):
    directory = config.output / "test"
    result, saved = _read(directory / "result.json"), _read(directory / "config.json")
    expected = json.loads(json.dumps(asdict(replace(config, output=directory)), default=str))
    if (
        saved["settings"] != expected
        or saved["source_sha256"] != protocol["source_sha256"]
        or saved["dependency_lock_sha256"] != protocol["dependency_lock_sha256"]
        or result["status"] != "succeeded"
        or result["phase"] != "test"
        or result["model_parameters"] != config.parameters
    ):
        raise ValueError("Saved Test differs from frozen settings/source")
    common = dict(
        manifest=saved["manifest"],
        evaluation=result["primary"],
        features_sha256=saved["features_sha256"],
        population=result["population"],
    )
    if common != protocol["common"]:
        raise ValueError("Saved Test input/population differs")
    if config.model == "ease" and protocol["ease_memory_budget_mb"] is not None:
        actual = {**config.parameters, "memory_budget_mb": protocol["ease_memory_budget_mb"]}
        if saved["execution"].get("training_parameters") != actual:
            raise ValueError("EASE execution budget differs from frozen protocol")
    verify_artifacts(directory, result["artifacts"])
    return result


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
