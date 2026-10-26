"""Rank Validation results, record review and freeze a selection."""

import json
from dataclasses import replace
from statistics import fmean, stdev

from src.infrastructure.research.search.artifacts import digest, read_sealed, seal
from src.infrastructure.research.search.state import load_study, require_unlocked, study_lock
from src.infrastructure.research.search.trials import read_features, verify_trial
from src.infrastructure.research.search_protocols import (
    data_fingerprint,
    load_data,
)
from src.infrastructure.research.store import read_value as read_json
from src.infrastructure.research.store import write_value as write_json


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
        if verify_trial(config, trial):
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


def select_best(config):
    with study_lock(config):
        require_unlocked(config)
        ranked = ranked_settings(config)
        study = load_study(config)
        selected = seal(
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
    selected = read_sealed(config.output / "selection.json")
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
        "features": read_features(config, data),
        "model": selected["model"],
        "parameters": selected["parameters"],
        "seed": selected["seeds"][0],
        "selection": selected,
    }


def review_selection(config, *, accepted, note=""):
    if type(accepted) is not bool:
        raise ValueError("確認結果を明示してください")
    with study_lock(config):
        require_unlocked(config)
        selected = load_selection(config)
        record = seal(
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
            read_sealed(config.output / "review.json")
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
        locked = seal(
            {
                **{k: v for k, v in selected.items() if k != "content_sha256"},
                "selection_sha256": selected["content_sha256"],
                "review_sha256": review["content_sha256"],
                "test_sha256": data_fingerprint(config, config.test_dataset),
            }
        )
        existing = config.output / "locked-selection.json"
        if existing.exists() and read_sealed(existing) != locked:
            raise ValueError("固定済み条件は変更できません")
        write_json(existing, locked)
        return locked


def load_locked_selection(config):
    selected = load_selection(config)
    locked = read_sealed(config.output / "locked-selection.json")
    review = read_sealed(config.output / "review.json")
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
