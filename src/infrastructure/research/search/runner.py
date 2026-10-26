"""Run smoke checks and budgeted Validation searches."""

import itertools
import math
import time
from dataclasses import replace

from src.infrastructure.recommenders import create_recommender
from src.infrastructure.research.search.artifacts import digest
from src.infrastructure.research.search.state import (
    context,
    open_study,
    plan_config,
    require_unlocked,
    study_lock,
)
from src.infrastructure.research.search.trials import run_trial
from src.infrastructure.research.store import read_value as read_json
from src.infrastructure.research.store import write_value as write_json


def _plans(config):
    names = sorted(config.grid)
    for values in itertools.product(*(config.grid[n] for n in names)):
        parameters = {**config.parameters, **dict(zip(names, values, strict=True))}
        for seed in config.seeds:
            yield parameters, seed


def smoke(config, *, factory=create_recommender):
    with study_lock(config):
        require_unlocked(config)
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
        study = open_study(config, conditions)
        study.update(status="running", configuration=plan_config(config))
        params, seed = next(_plans(config))
        key, entry, reused = run_trial(config, study, data, params, seed, factory, phase="smoke")
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
        require_unlocked(config)
        data, conditions = context(config)
        study = open_study(config, conditions)
        if study.get("smoke", {}).get("status") != "succeeded":
            raise ValueError("Smoke Testを先に実行してください")
        _validate_parent(config, conditions)
        planned_count = math.prod(len(v) for v in config.grid.values()) * len(config.seeds)
        study.update(
            status="running",
            configuration=plan_config(config),
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
            key, entry, reused = run_trial(config, study, data, parameters, seed, factory)
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
