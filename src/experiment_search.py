"""Validation探索・選択・定性的確認・固定・最終Testの共通操作。"""

import argparse
import fcntl
import hashlib
import itertools
import json
import math
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import fmean, stdev

from src.evaluation.benchmark import _source_sha256, run_benchmark, save_report
from src.evaluation.tracking import ExperimentRun, TrackingConfig, TrackingResult
from src.recommenders import create_recommender
from src.search_protocols import (
    V2_OBJECTIVES,
    data_fingerprint,
    evaluate_trial,
    load_data,
    validate_test_training,
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def file_hash(path):
    from src.experiment_store import value_hash

    return value_hash(path)


def read_json(path):
    from src.experiment_store import read_value

    return read_value(path)


def write_json(path, value):
    from src.experiment_store import write_value

    write_value(path, value)


@dataclass(frozen=True)
class StudyConfig:
    path: Path
    name: str
    validation_dataset: Path
    test_dataset: Path | None
    output: Path
    model: str
    parameters: dict
    grid: dict
    seeds: tuple[int, ...]
    stage: str
    metric: str | None
    primary_k: int
    direction: str
    max_trials: int
    time_budget_seconds: float | None
    parent: Path | None
    features: Path | None
    tracking: TrackingConfig
    snapshot_id: str | None = None
    split_id: str | None = None
    smoke_users: int = 3
    smoke_train_users: int = 50
    protocol: str = "binary-v1"
    evaluation: object | None = None
    task: str | None = None


def load_study_config(path):
    from src.configuration import load_settings

    path = Path(path).resolve()
    raw = load_settings(path)
    e, m, s, objective = (raw.get(x, {}) for x in ("experiment", "model", "search", "selection"))

    def resolve(value):
        return (path.parent / value).resolve() if value else None

    try:
        name, model = e["name"], m["name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(model, str)
            or not model.strip()
        ):
            raise ValueError("experiment/model name is required")
        parameters, grid = m.get("parameters", {}), s.get("grid", {})
        if (
            not isinstance(parameters, dict)
            or not isinstance(grid, dict)
            or parameters.keys() & grid.keys()
        ):
            raise ValueError("固定パラメータと探索パラメータを重複させないでください")
        for values in grid.values():
            if (
                not isinstance(values, list)
                or not values
                or len({digest(v) for v in values}) != len(values)
            ):
                raise ValueError("grid values must be nonempty unique lists")
        digest(parameters)
        stage = s.get("stage", "A")
        if stage not in {"A", "B", "C"} or (stage == "A" and len(grid) > 1):
            raise ValueError("Stage Aは1変数以下。組合せはStage Bを指定してください")
        if stage == "C" and not s.get("parent"):
            raise ValueError("Stage Cは親探索のstudy.jsonが必要です")
        seeds = s.get("seeds", [42])
        if (
            not isinstance(seeds, list)
            or not seeds
            or any(type(x) is not int or x < 0 for x in seeds)
            or len(set(seeds)) != len(seeds)
        ):
            raise ValueError("seeds must be unique non-negative integers")
        budget = s.get("max_trials", 100)
        seconds = s.get("time_budget_seconds")
        if type(budget) is not int or budget < 1:
            raise ValueError("max_trials must be positive")
        smoke_users, smoke_train_users = s.get("smoke_users", 3), s.get("smoke_train_users", 50)
        if any(type(v) is not int or v < 1 for v in (smoke_users, smoke_train_users)):
            raise ValueError("smoke_users / smoke_train_users must be positive integers")
        if seconds is not None and (
            type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0
        ):
            raise ValueError("time budget must be finite and positive")
        metric, k, direction = (
            objective.get("metric"),
            objective.get("k", 10),
            objective.get("direction", "maximize"),
        )
        protocol, task, evaluation = e.get("protocol", "binary-v1"), objective.get("task"), None
        if protocol not in {"binary-v1", "listening-v2"}:
            raise ValueError("unknown experiment protocol")
        allowed = {None, "ndcg", "recall"}
        if protocol == "listening-v2":
            from src.listening_types import ListeningConfig

            if task is not None and task not in V2_OBJECTIVES:
                raise ValueError("invalid selection.task")
            if metric is not None and (task is None or metric not in V2_OBJECTIVES[task]):
                raise ValueError("selection.task / metric must explicitly match a v2 task")
            options = dict(raw.get("evaluation", {}))
            for field in ("window_days", "ks", "day_weights"):
                if field in options:
                    options[field] = tuple(options[field])
            if "primary_k" in options and options["primary_k"] != k:
                raise ValueError("primary_k must match selection.k")
            options["primary_k"] = k
            evaluation = ListeningConfig(**options)
            allowed = {None, *V2_OBJECTIVES.get(task, ())}
        elif task is not None:
            raise ValueError("selection.task requires listening-v2")
        if (
            metric not in allowed
            or type(k) is not int
            or not 1 <= k <= 20
            or direction not in {"maximize", "minimize"}
        ):
            raise ValueError("主指標はndcg/recall、K=1..20、directionを指定してください")
        return StudyConfig(
            path,
            name,
            resolve(e["validation_dataset"]),
            resolve(e.get("test_dataset")),
            resolve(e["output"]),
            model,
            parameters,
            grid,
            tuple(seeds),
            stage,
            metric,
            k,
            direction,
            budget,
            seconds,
            resolve(s.get("parent")),
            resolve(e.get("features")),
            TrackingConfig.from_settings(raw.get("tracking")),
            e.get("snapshot_id"),
            e.get("split_id"),
            smoke_users,
            smoke_train_users,
            protocol,
            evaluation,
            task,
        )
    except (KeyError, TypeError) as error:
        raise ValueError("探索TOMLのexperiment/model/search設定を確認してください") from error


def context(config):
    data = load_data(config, config.validation_dataset)
    provenance = data.provenance or {}
    if provenance.get("phase") != "validation":
        raise ValueError("Validation入力だけを探索できます。01でphaseを確認してください")
    if not provenance.get("snapshot_id") or not provenance.get("split_id"):
        raise ValueError("比較条件: Snapshot / Splitの出典が必要です")
    if (config.snapshot_id and config.snapshot_id != provenance["snapshot_id"]) or (
        config.split_id and config.split_id != provenance["split_id"]
    ):
        raise ValueError("比較条件不一致: TOMLのSnapshot / Split")
    lock = Path(__file__).resolve().parents[1] / "uv.lock"
    conditions = {
        "dataset_id": data.dataset_id,
        "validation_sha256": data.sha256,
        "snapshot_id": provenance["snapshot_id"],
        "split_id": provenance["split_id"],
        "dataset_version_id": provenance.get("dataset_version_id"),
        "protocol": "explicit-json-binary-unseen-v1",
        "generation_k": 20,
        "source_sha256": _source_sha256(),
        "dependency_lock_sha256": file_hash(lock),
        "primary_metric": config.metric,
        "primary_k": config.primary_k,
        "direction": config.direction,
        "features_sha256": file_hash(config.features) if config.features else None,
    }
    if config.protocol == "listening-v2":
        source_files = data.manifest.get("source_files", {})
        if (
            set(source_files) != {"train", "validation"}
            or len(data.train_interactions) != source_files["train"]["row_count"]
        ):
            raise ValueError("比較条件不一致: Validationのsource / learning")
        conditions.update(
            protocol="listening-v2",
            evaluation=json.loads(json.dumps(asdict(config.evaluation))),
            primary_task=config.task,
            source_files=source_files,
            temporal=data.manifest["temporal"],
        )
    return data, conditions


@contextmanager
def study_lock(config):
    config.output.mkdir(parents=True, exist_ok=True)
    with (config.output / ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("探索は実行中です。同じ出力への同時書込みはできません") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _plan_configuration(config):
    return {
        "grid": config.grid,
        "parameters": config.parameters,
        "seeds": list(config.seeds),
        "stage": config.stage,
    }


def load_study(config, *, allow_plan_change=False):
    _, conditions = context(config)
    path = config.output / "study.json"
    from src.experiment_store import exists

    if not exists(path):
        return None
    result = read_json(path)
    saved = result.get("conditions", {})
    comparable = dict(conditions)
    if (config.output / "result.json").exists():
        # 原本コード版を保持したまま過去結果を閲覧 / 再実行は_unlockedで拒否
        comparable["source_sha256"] = saved.get("source_sha256")
    if saved != comparable or result.get("model") != config.model:
        raise ValueError(
            "比較条件不一致。データ・コード・主指標を確認し、別の出力先を指定してください"
        )
    if not allow_plan_change and result.get("configuration") != _plan_configuration(config):
        raise ValueError("探索範囲・seedが変わっています。searchを実行してから再選択してください")
    return result


def _study(config, conditions):
    current = load_study(config, allow_plan_change=True)
    return current or {
        "schema_version": 1,
        "name": config.name,
        "model": config.model,
        "phase": "validation",
        "conditions": conditions,
        "status": "not_run",
        "trials": {},
        "history": [],
        "planned": [],
        "planned_trials": 0,
    }


def _plans(config):
    names = sorted(config.grid)
    for values in itertools.product(*(config.grid[n] for n in names)):
        parameters = {**config.parameters, **dict(zip(names, values, strict=True))}
        for seed in config.seeds:
            yield parameters, seed


def _features(config, data):
    if config.features is None:
        return None
    from src.features import load_features

    return load_features(
        config.features,
        dataset_id=data.dataset_id,
        snapshot_id=(data.provenance or {}).get("snapshot_id"),
    )


def _verified_trial(config, entry):
    from src.experiment_store import exists as artifact_exists

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


def _trial_key(config, conditions, dataset_sha256, parameters, seed, phase):
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


def _trial(config, study, data, parameters, seed, factory, *, phase="validation"):
    key = _trial_key(config, study["conditions"], data.sha256, parameters, seed, phase)
    prior = study["trials"].get(key)
    if prior and _verified_trial(config, prior):
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
            from src.features import FEATURE_MODELS

            features = _features(config, data)
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


def _unlocked(config):
    if (config.output / "result.json").exists():
        saved = read_json(config.output / "study.json")
        if saved["conditions"]["source_sha256"] != _source_sha256():
            raise ValueError(
                "保存時とコードが異なります / 閲覧は可能 / 再評価は別のoutputを指定してください"
            )
    if (config.output / "locked-selection.json").exists():
        raise ValueError("固定済み設定は探索・再選択できません。別の実験として開始してください")


def smoke(config, *, factory=create_recommender):
    with study_lock(config):
        _unlocked(config)
        data, conditions = context(config)
        # 小規模動作確認は別のphaseとデータhashを持ち、Validationの性能として再利用しない。
        users = list(dict.fromkeys(r.user_id for r in data.requests))[: config.smoke_users]
        requests = tuple(r for r in data.requests if r.user_id in users)
        request_ids = {r.request_id for r in requests}
        train_users = set(users) | set(
            list(dict.fromkeys(x.user_id for x in data.train_interactions))[
                : config.smoke_train_users
            ]
        )
        interactions = tuple(x for x in data.train_interactions if x.user_id in train_users)
        extra = (
            {}
            if config.protocol == "listening-v2"
            else {
                "ground_truth": tuple(t for t in data.ground_truth if t.request_id in request_ids)
            }
        )
        data = replace(
            data,
            requests=requests,
            **extra,
            train_interactions=interactions,
            sha256=digest(
                {
                    "source": data.sha256,
                    "requests": [r.request_id for r in requests],
                    "train_users": sorted(train_users),
                }
            ),
        )
        study = _study(config, conditions)
        study.update(status="running", configuration=_plan_configuration(config))
        params, seed = next(_plans(config))
        key, entry, reused = _trial(config, study, data, params, seed, factory, phase="smoke")
        study["smoke"] = {"status": entry["status"], "trial_id": key}
        study["status"] = "smoke_passed" if entry["status"] == "succeeded" else "failed"
        write_json(config.output / "study.json", study)
        return {**study["smoke"], "reused": reused}


def _validate_parent(config, conditions):
    if config.stage != "C":
        return
    parent = read_json(config.parent)
    if (
        parent.get("status") != "succeeded"
        or parent.get("conditions") != conditions
        or parent.get("model") != config.model
    ):
        raise ValueError("Stage Cの親探索は同じ条件で完了している必要があります")
    grid = parent["history"][-1]["grid"]
    if parent["history"][-1]["parameters"] != config.parameters:
        raise ValueError("Stage Cの固定パラメータは親探索と同じにしてください")
    if set(grid) != set(config.grid):
        raise ValueError("Stage Cは親探索と同じパラメータを絞り込んでください")
    for key, values in config.grid.items():
        before = grid[key]
        numeric = all(type(x) in (int, float) for x in before + values)
        if numeric and not all(min(before) <= x <= max(before) for x in values):
            raise ValueError("Stage Cの数値範囲は親探索の内側にしてください")
        if not numeric and any(v not in before for v in values):
            raise ValueError("Stage Cのカテゴリは親探索の候補から選んでください")


def run_search(config, *, factory=create_recommender):
    with study_lock(config):
        _unlocked(config)
        data, conditions = context(config)
        study = _study(config, conditions)
        if study.get("smoke", {}).get("status") != "succeeded":
            raise ValueError("Smoke Testを先に実行してください")
        _validate_parent(config, conditions)
        planned_count = math.prod(len(v) for v in config.grid.values()) * len(config.seeds)
        study.update(
            status="running",
            configuration=_plan_configuration(config),
            planned=[],
            planned_trials=planned_count,
            executed_trials=0,
            reused_trials=0,
        )
        study["history"].append(
            {
                "stage": config.stage,
                "grid": config.grid,
                "parameters": config.parameters,
                "seeds": config.seeds,
                "max_trials": config.max_trials,
                "time_budget_seconds": config.time_budget_seconds,
            }
        )
        start = time.perf_counter()
        for index, (parameters, seed) in enumerate(_plans(config)):
            if index >= config.max_trials or (
                config.time_budget_seconds
                and time.perf_counter() - start >= config.time_budget_seconds
            ):
                break
            key, entry, reused = _trial(config, study, data, parameters, seed, factory)
            study["planned"].append(key)
            study["reused_trials" if reused else "executed_trials"] += 1
            study["elapsed_seconds"] = time.perf_counter() - start
            write_json(config.output / "study.json", study)
        complete = len(study["planned"]) == planned_count
        failed = any(study["trials"][key]["status"] != "succeeded" for key in study["planned"])
        study["status"] = "failed" if failed else "succeeded" if complete else "incomplete"
        study["elapsed_seconds"] = time.perf_counter() - start
        study["successful_trials"] = sum(
            study["trials"][key]["status"] == "succeeded" for key in study["planned"]
        )
        study["failed_trials"] = sum(
            study["trials"][key]["status"] == "failed" for key in study["planned"]
        )
        write_json(config.output / "study.json", study)
        return study


def trial_rows(config):
    study = load_study(config)
    if not study:
        return []
    rows = []
    for key in study["planned"]:
        trial = study["trials"][key]
        row = {
            "trial_id": key,
            "model": config.model,
            "parameters": trial["parameters"],
            "seed": trial["seed"],
            "status": trial["status"],
            "seconds": trial.get("seconds"),
            "error_type": trial.get("error_type"),
        }
        if _verified_trial(config, trial):
            curves = read_json(config.output / trial["curves"])
            row.update(next(x for x in curves["metrics"] if x["k"] == config.primary_k))
            row.update(curves["efficiency"])
        rows.append(row)
    return rows


def ranked_settings(config):
    study = load_study(config)
    if not study or study["status"] != "succeeded":
        raise ValueError("探索が完了していません。失敗・予算・実行状態を確認してください")
    if config.metric is None:
        raise ValueError("主指標が未指定です。開始前に指定した別の実験を用意してください")
    grouped = {}
    for row in trial_rows(config):
        grouped.setdefault(digest(row["parameters"]), []).append(row)
    result = []
    for rows in grouped.values():
        values = [r.get(config.metric) for r in rows]
        if {r["seed"] for r in rows} != set(config.seeds) or any(v is None for v in values):
            raise ValueError("全seedの評価が完了していません。01で正解数も確認してください")
        result.append(
            {
                "parameters": rows[0]["parameters"],
                "score": fmean(values),
                "seed_std": stdev(values) if len(values) > 1 else None,
                "seeds": list(config.seeds),
                "trial_ids": [r["trial_id"] for r in rows],
                "mean_seconds": fmean(r["seconds"] for r in rows),
            }
        )
    return sorted(
        result,
        key=lambda r: (
            (-1 if config.direction == "maximize" else 1) * r["score"],
            json.dumps(r["parameters"], sort_keys=True),
        ),
    )


def _seal(value):
    return {**value, "content_sha256": digest(value)}


def _read_sealed(path):
    if not Path(path).exists():
        raise ValueError("設定の選択・固定がまだです。02_analysisで状態を確認してください")
    raw = read_json(path)
    checksum = raw.pop("content_sha256", None)
    if checksum != digest(raw):
        raise ValueError("比較条件不一致: 選択artifactが改変されています")
    return {**raw, "content_sha256": checksum}


def select_best(config):
    with study_lock(config):
        _unlocked(config)
        ranked = ranked_settings(config)
        study = load_study(config)
        selected = _seal(
            {
                "schema_version": 1,
                "phase": "validation",
                "model": config.model,
                "conditions": study["conditions"],
                "objective": {
                    "metric": config.metric,
                    "k": config.primary_k,
                    "direction": config.direction,
                    "task": config.task,
                },
                **ranked[0],
                "runner_up": ranked[1] if len(ranked) > 1 else None,
                "study_plan_sha256": digest(study["history"][-1]),
            }
        )
        write_json(config.output / "selection.json", selected)
        return selected


def load_selection(config):
    study = load_study(config)
    selected = _read_sealed(config.output / "selection.json")
    if (
        not study
        or study["conditions"] != selected["conditions"]
        or digest(study["history"][-1]) != selected["study_plan_sha256"]
    ):
        raise ValueError("比較条件不一致: 探索を更新したので02で再選択してください")
    ranked = ranked_settings(config)
    if (
        selected["parameters"] != ranked[0]["parameters"]
        or selected["trial_ids"] != ranked[0]["trial_ids"]
    ):
        raise ValueError("比較条件不一致: 選択と探索結果")
    return selected


def inspect_selected(config):
    selected = load_selection(config)
    data = load_data(config, config.validation_dataset)
    if config.protocol == "listening-v2":
        task = "repeat" if config.task == "repeat" else "new"
        data = replace(data, requests=tuple(r for r in data.requests if r.task == task))
    return {
        "dataset": data,
        "features": _features(config, data),
        "model": selected["model"],
        "parameters": selected["parameters"],
        "seed": selected["seeds"][0],
        "selection": selected,
    }


def review_selection(config, *, accepted, note=""):
    if type(accepted) is not bool:
        raise ValueError("確認結果を明示してください")
    with study_lock(config):
        _unlocked(config)
        selected = load_selection(config)
        record = _seal(
            {
                "selection_sha256": selected["content_sha256"],
                "phase": "validation",
                "accepted": accepted,
                "note": note,
            }
        )
        write_json(config.output / "review.json", record)
        return record


def freeze_selection(config):
    with study_lock(config):
        selected = load_selection(config)
        review = (
            _read_sealed(config.output / "review.json")
            if (config.output / "review.json").exists()
            else None
        )
        if (
            not review
            or not review["accepted"]
            or review["selection_sha256"] != selected["content_sha256"]
        ):
            raise ValueError("03_recommendationでValidationの推薦を確認して記録してください")
        if config.test_dataset is None:
            raise ValueError("Test入力をTOMLに指定してください")
        locked = _seal(
            {
                **{k: v for k, v in selected.items() if k != "content_sha256"},
                "selection_sha256": selected["content_sha256"],
                "review_sha256": review["content_sha256"],
                "test_sha256": data_fingerprint(config, config.test_dataset),
            }
        )
        existing = config.output / "locked-selection.json"
        if existing.exists() and _read_sealed(existing) != locked:
            raise ValueError("固定済み条件は変更できません")
        write_json(existing, locked)
        return locked


def _test_data(config, selected, locked):
    if (
        config.test_dataset is None
        or data_fingerprint(config, config.test_dataset) != locked["test_sha256"]
    ):
        raise ValueError("比較条件不一致: 固定後にTest入力が変わっています")
    test = load_data(config, config.test_dataset)
    validation = load_data(config, config.validation_dataset)
    p = test.provenance or {}
    if p.get("phase") != "test" or p.get("training_periods") != ["train", "validation"]:
        raise ValueError("TestはTrain + Validationで再学習する入力が必要です")
    for name in ("snapshot_id", "split_id", "dataset_version_id"):
        if p.get(name) != selected["conditions"].get(name):
            raise ValueError("比較条件不一致: Testの出典")
    if test.dataset_id != validation.dataset_id:
        raise ValueError("比較条件不一致: Test dataset")
    if config.protocol == "listening-v2":
        validate_test_training(config, test, validation)
        return test
    before = Counter((x.user_id, x.item_id) for x in validation.train_interactions)
    after = Counter((x.user_id, x.item_id) for x in test.train_interactions)
    requests = {r.request_id: r for r in validation.requests}
    observed = {
        (requests[truth.request_id].user_id, item)
        for truth in validation.ground_truth
        for item in truth.relevant_item_ids
    }
    before.update(observed)
    if any(after[key] < count for key, count in before.items()):
        raise ValueError("Test学習履歴にTrain + Validationが反映されていません")
    by_user = {}
    for user, item in after:
        by_user.setdefault(user, set()).add(item)
    if any(
        set(request.history) != by_user.get(request.user_id, set()) for request in test.requests
    ):
        raise ValueError("Test要求の履歴にTrain + Validationの既聴曲をすべて反映してください")
    return test


def load_locked_selection(config):
    selected = load_selection(config)
    locked = _read_sealed(config.output / "locked-selection.json")
    review = _read_sealed(config.output / "review.json")
    if (
        locked["selection_sha256"] != selected["content_sha256"]
        or locked["review_sha256"] != review["content_sha256"]
        or review["selection_sha256"] != selected["content_sha256"]
        or review["accepted"] is not True
        or config.test_dataset is None
        or data_fingerprint(config, config.test_dataset) != locked["test_sha256"]
    ):
        raise ValueError("比較条件不一致: 固定済み選択・確認記録・Test入力")
    return locked


def load_final_test(config):
    """状態表示・比較・再実行で同じ検証を行う。Testの正解内容は読まない。"""
    locked = load_locked_selection(config)
    final = read_json(config.output / "final-test.json")
    conditions = {**locked["conditions"], "test_sha256": locked["test_sha256"]}
    if (
        final.get("locked_sha256") != locked["content_sha256"]
        or final.get("conditions") != conditions
        or final.get("model") != config.model
        or final.get("phase") != "test"
        or final.get("status") not in {"running", "failed", "succeeded"}
    ):
        raise ValueError("比較条件不一致: 最終Test")
    expected = {
        _trial_key(config, conditions, locked["test_sha256"], locked["parameters"], seed, "test")
        for seed in locked["seeds"]
    }
    if not set(final["trials"]).issubset(expected) or (
        final["status"] == "succeeded" and set(final["trials"]) != expected
    ):
        raise ValueError("比較条件不一致: Testのseedが不足・不正です")
    for key, trial in final["trials"].items():
        if (
            trial["phase"] != "test"
            or trial["parameters"] != locked["parameters"]
            or trial["seed"] not in locked["seeds"]
            or key
            != _trial_key(
                config,
                conditions,
                locked["test_sha256"],
                trial["parameters"],
                trial["seed"],
                "test",
            )
        ):
            raise ValueError("比較条件不一致: Testのtrial")
        verified = _verified_trial(config, trial)
        if final["status"] == "succeeded" and not verified:
            raise ValueError("比較条件不一致: Testのtrialが未成功です")
    return final


def run_final_test(config, *, factory=create_recommender):
    with study_lock(config):
        locked = load_locked_selection(config)
        selected = load_selection(config)
        data = _test_data(config, selected, locked)
        target = config.output / "final-test.json"
        if target.exists():
            prior = load_final_test(config)
            if prior["status"] == "succeeded":
                return {**prior, "reused": True}
        else:
            prior = {}
        result = {
            "schema_version": 1,
            "phase": "test",
            "status": "running",
            "locked_sha256": locked["content_sha256"],
            "conditions": {**selected["conditions"], "test_sha256": data.sha256},
            "model": config.model,
            "trials": prior.get("trials", {}),
        }
        write_json(target, result)
        for seed in selected["seeds"]:
            _trial(config, result, data, selected["parameters"], seed, factory, phase="test")
            write_json(target, result)
        result["status"] = (
            "succeeded"
            if all(t["status"] == "succeeded" for t in result["trials"].values())
            else "failed"
        )
        write_json(target, result)
        return result


def main():
    parser = argparse.ArgumentParser(
        description="Validation exploration / selection / locked final Test"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "action", choices=("smoke", "search", "select", "review", "freeze", "test", "status")
    )
    parser.add_argument("--accept", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    try:
        from src.search_analysis import workflow_state

        config = load_study_config(args.config)
        actions = {
            "smoke": smoke,
            "search": run_search,
            "select": select_best,
            "freeze": freeze_selection,
            "test": run_final_test,
            "status": workflow_state,
        }
        result = (
            review_selection(config, accepted=args.accept, note=args.note)
            if args.action == "review"
            else actions[args.action](config)
        )
        if args.action != "status" and config.protocol == "listening-v2":
            from src.experiment_store import compact

            with study_lock(config):
                compact(config)
        if (
            args.action == "search"
            and config.protocol == "listening-v2"
            and result.get("status") == "succeeded"
            and len(
                {
                    str(t["parameters"])
                    for t in result["trials"].values()
                    if t["phase"] == "validation"
                }
            )
            == 1
        ):
            from src.evaluation_report import build_report

            build_report(config.path)
        state = workflow_state(config)
        if args.action != "status":
            with (config.output / "run.log").open("a") as log:
                log.write(
                    json.dumps(
                        {"action": args.action, "status": state["status"]}, ensure_ascii=False
                    )
                    + "\n"
                )
        print(
            json.dumps(
                {
                    "action": args.action,
                    "status": (result or {}).get("status", "saved"),
                    "output": str(config.output),
                    "workflow": state,
                },
                ensure_ascii=False,
            )
        )
        return (
            1
            if (result and result.get("status") in {"failed", "incomplete"})
            or state["status"] == "mismatch"
            else 0
        )
    except Exception as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
