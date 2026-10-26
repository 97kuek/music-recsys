"""Validate frozen Test inputs and execute or resume final evaluation."""

from collections import Counter

from src.infrastructure.recommenders import create_recommender
from src.infrastructure.research.search.selection import load_locked_selection, load_selection
from src.infrastructure.research.search.state import study_lock
from src.infrastructure.research.search.trials import run_trial, trial_key, verify_trial
from src.infrastructure.research.search_protocols import (
    data_fingerprint,
    load_data,
    validate_test_training,
)
from src.infrastructure.research.store import read_value as read_json
from src.infrastructure.research.store import write_value as write_json


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
        trial_key(config, conditions, locked["test_sha256"], locked["parameters"], seed, "test")
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
            != trial_key(
                config,
                conditions,
                locked["test_sha256"],
                trial["parameters"],
                trial["seed"],
                "test",
            )
        ):
            raise ValueError("比較条件不一致: Testのtrial")
        verified = verify_trial(config, trial)
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
            run_trial(config, result, data, selected["parameters"], seed, factory, phase="test")
            write_json(target, result)
        result["status"] = (
            "succeeded"
            if all(t["status"] == "succeeded" for t in result["trials"].values())
            else "failed"
        )
        write_json(target, result)
        return result
