"""Local data, provenance, locking and persistence for the search use case."""

import itertools
import math
from contextlib import contextmanager
from dataclasses import replace

from backend.domain.experiments import RunStatus
from backend.domain.search import SearchStatus, TrialOutcome, TrialSpec
from backend.infrastructure.recommenders import create_recommender
from backend.infrastructure.research.search.artifacts import digest
from backend.infrastructure.research.search.state import (
    context,
    open_study,
    plan_config,
    require_unlocked,
    study_lock,
)
from backend.infrastructure.research.search.trials import run_trial
from backend.infrastructure.research.store import read_value as read_json
from backend.infrastructure.research.store import write_value as write_json


def _plans(config):
    names = sorted(config.grid)
    for values in itertools.product(*(config.grid[n] for n in names)):
        parameters = {**config.parameters, **dict(zip(names, values, strict=True))}
        for seed in config.seeds:
            yield parameters, seed


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


def smoke_data(config, data):
    # 小規模動作確認は別のphaseとデータhashを持ち、Validationの性能として再利用しない。
    users = list(dict.fromkeys(r.user_id for r in data.requests))[: config.smoke_users]
    requests = tuple(r for r in data.requests if r.user_id in users)
    request_ids = {r.request_id for r in requests}
    train_users = set(users) | set(
        list(dict.fromkeys(x.user_id for x in data.train_interactions))[: config.smoke_train_users]
    )
    interactions = tuple(x for x in data.train_interactions if x.user_id in train_users)
    extra = (
        {}
        if config.protocol == "listening-v2"
        else {"ground_truth": tuple(t for t in data.ground_truth if t.request_id in request_ids)}
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
    return data


class LocalSearch:
    def __init__(self, factory=create_recommender):
        self._factory = factory

    @contextmanager
    def open(self, config, *, smoke):
        with study_lock(config):
            require_unlocked(config)
            data, conditions = context(config)
            study = open_study(config, conditions)
            if smoke:
                data = smoke_data(config, data)
            yield _SearchSession(config, data, study, smoke, self._factory)


class _SearchSession:
    def __init__(self, config, data, study, smoke, factory):
        self._config, self._data, self._study = config, data, study
        self._smoke, self._factory = smoke, factory
        self._outcome = None

    @property
    def planned(self):
        return math.prod(len(v) for v in self._config.grid.values()) * len(self._config.seeds)

    @property
    def max_trials(self):
        return self._config.max_trials

    @property
    def time_budget(self):
        return self._config.time_budget_seconds

    @property
    def smoke_passed(self):
        return self._study.get("smoke", {}).get("status") == "succeeded"

    def prepare(self):
        config = self._config
        if not self._smoke:
            _validate_parent(config, self._study["conditions"])
            self._study["history"].append(
                dict(
                    stage=config.stage,
                    grid=config.grid,
                    parameters=config.parameters,
                    seeds=config.seeds,
                    max_trials=config.max_trials,
                    time_budget_seconds=config.time_budget_seconds,
                )
            )
        self._study["configuration"] = plan_config(config)

    def plans(self):
        for parameters, seed in _plans(self._config):
            yield TrialSpec(parameters, seed)

    def execute(self, trial):
        key, entry, reused = run_trial(
            self._config,
            self._study,
            self._data,
            trial.parameters,
            trial.seed,
            self._factory,
            phase="smoke" if self._smoke else "validation",
        )
        self._outcome = TrialOutcome(key, RunStatus(entry["status"]), reused)
        return self._outcome

    def checkpoint(self, state):
        study = self._study
        study["status"] = state.status.value
        if self._smoke:
            if self._outcome is not None:
                study["smoke"] = {
                    "status": self._outcome.status.value,
                    "trial_id": self._outcome.key,
                }
        else:
            study.update(
                planned=[trial.key for trial in state.outcomes],
                planned_trials=state.planned,
                executed_trials=state.executed,
                reused_trials=state.reused,
                elapsed_seconds=state.elapsed_seconds,
            )
            if state.status != SearchStatus.RUNNING:
                study.update(successful_trials=state.successful, failed_trials=state.failed)
        write_json(self._config.output / "study.json", study)

    def result(self):
        if self._smoke:
            return {**self._study["smoke"], "reused": self._outcome.reused}
        return self._study
