"""Train, evaluate, persist and verify one trial."""

import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path

from src.infrastructure.evaluation.benchmark import run_benchmark, save_report
from src.infrastructure.evaluation.tracking import ExperimentRun, TrackingResult
from src.infrastructure.research.search.artifacts import digest
from src.infrastructure.research.search_protocols import (
    evaluate_trial,
)
from src.infrastructure.research.store import value_hash as file_hash
from src.infrastructure.research.store import write_value as write_json


def read_features(config, data):
    if config.features is None:
        return None
    from src.infrastructure.datasets.features import load_features

    return load_features(
        config.features,
        dataset_id=data.dataset_id,
        snapshot_id=(data.provenance or {}).get("snapshot_id"),
    )


def verify_trial(config, entry):
    from src.infrastructure.research.store import exists as artifact_exists

    if entry.get("status") != "succeeded":
        return False
    for role in ("report", "curves"):
        p = (config.output / entry[role]).resolve()
        if (
            not p.is_relative_to(config.output)
            or not artifact_exists(p)
            or file_hash(p) != entry[f"{role}_sha256"]
        ):
            raise ValueError("比較条件不一致: 保存済みtrialの改変・欠損")
    return True


def trial_key(config, conditions, dataset_sha256, parameters, seed, phase):
    return digest(
        {
            "conditions": conditions,
            "dataset_sha256": dataset_sha256,
            "model": config.model,
            "parameters": parameters,
            "seed": seed,
            "phase": phase,
        }
    )


def run_trial(config, study, data, parameters, seed, factory, *, phase="validation"):
    key = trial_key(config, study["conditions"], data.sha256, parameters, seed, phase)
    prior = study["trials"].get(key)
    if prior and verify_trial(config, prior):
        return key, prior, True
    if prior and prior.get("status") == "running" and prior.get("attempts"):
        prior["attempts"][-1] = {"status": "failed", "error_type": "InterruptedRun"}
    entry = {
        "id": key,
        "status": "running",
        "phase": phase,
        "parameters": parameters,
        "seed": seed,
        "attempts": (prior or {}).get("attempts", []) + [{"status": "running"}],
    }
    study["trials"][key] = entry
    write_json(config.output / ("final-test.json" if phase == "test" else "study.json"), study)
    started = time.perf_counter()
    experiment = (
        ExperimentRun(
            config.tracking,
            params={
                **study["conditions"],
                "model": config.model,
                "parameters": parameters,
                "seed": seed,
                "phase": phase,
                "trial_id": key,
            },
        )
        if config.tracking.enabled
        else None
    )
    from contextlib import nullcontext

    try:
        with experiment if experiment is not None else nullcontext() as active:
            start = time.perf_counter()
            from src.domain.recommendation.features import FEATURE_MODELS

            features = read_features(config, data)
            feature_args = {"features": features} if config.model in FEATURE_MODELS else {}
            model = factory(
                config.model,
                data.train_interactions,
                parameters=parameters,
                seed=seed,
                **feature_args,
            )
            training_seconds = time.perf_counter() - start
            evaluated = evaluate_trial(
                config,
                data,
                model,
                parameters,
                seed,
                training_seconds,
                digest(study["conditions"]),
                features,
                run_benchmark,
            )
            report, curves, valid = evaluated.report, evaluated.curves, evaluated.valid
            if not valid:
                entry["error_type"] = evaluated.error_type
                if active:
                    active.mark_failed()
            if active:
                metrics = next(r for r in curves["metrics"] if r["k"] == config.primary_k)
                active.log_metrics(
                    {k: v for k, v in metrics.items() if type(v) in (int, float) and k != "k"}
                )
                active.log_metrics(curves["efficiency"])
                with tempfile.TemporaryDirectory() as temp:
                    aggregate = Path(temp) / "report.json"
                    write_json(
                        aggregate,
                        {
                            "metadata": evaluated.metadata,
                            "summary": evaluated.summary,
                            "curves": curves["metrics"],
                            "efficiency": curves["efficiency"],
                        },
                    )
                    active.log_artifact(aggregate)
            # 保存・MLflowへのリンクもtrialの成功条件に含める。
            anticipated = (
                TrackingResult(
                    active.run_id, active.experiment_id, "FINISHED" if valid else "FAILED"
                )
                if active
                else None
            )
            report = replace(report, tracking=anticipated)
            directory = save_report(
                report, config.output / "trials" / key, tracking=config.tracking
            )
            curve_path = directory / "curves.json"
            write_json(curve_path, curves)
            report_sha256, curves_sha256 = (
                file_hash(directory / "results.json"),
                file_hash(curve_path),
            )
            if active:
                active.log_metrics({"trial_seconds": time.perf_counter() - started})
        tracking_result = experiment.result if experiment else None
        entry.update(
            status="succeeded" if valid else "failed",
            report=str((directory / "results.json").relative_to(config.output)),
            curves=str(curve_path.relative_to(config.output)),
            report_sha256=report_sha256,
            curves_sha256=curves_sha256,
            tracking=asdict(tracking_result) if tracking_result else None,
        )
    except Exception as error:
        entry.update(status="failed", error_type=type(error).__name__)
        if experiment and experiment.run_id:
            try:
                experiment.log_metrics({"trial_seconds": time.perf_counter() - started})
            except Exception:
                pass  # 接続障害で再記録できない場合も元の失敗状態を保持する。
            entry["tracking"] = asdict(experiment.result)
    entry["seconds"] = time.perf_counter() - started
    entry["attempts"][-1] = {
        "status": entry["status"],
        "seconds": entry["seconds"],
        "error_type": entry.get("error_type"),
    }
    return key, entry, False
