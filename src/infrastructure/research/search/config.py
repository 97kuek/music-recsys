"""Read and validate experiment settings."""

import math
from dataclasses import dataclass
from pathlib import Path

from src.infrastructure.evaluation.tracking import TrackingConfig
from src.infrastructure.research.search.artifacts import digest
from src.infrastructure.research.search_protocols import (
    V2_OBJECTIVES,
)


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
    from src.infrastructure.research.configuration import load_settings

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
            from src.domain.evaluation.listening import ListeningConfig

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
