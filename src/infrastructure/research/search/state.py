"""Validate study provenance and guard concurrent or frozen writes."""

import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict

from src.infrastructure.provenance import source_hash
from src.infrastructure.research.search_protocols import (
    load_data,
)
from src.infrastructure.research.store import read_value as read_json
from src.infrastructure.research.store import value_hash as file_hash
from src.paths import PROJECT_ROOT


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
    lock = PROJECT_ROOT / "uv.lock"
    conditions = {
        "dataset_id": data.dataset_id,
        "validation_sha256": data.sha256,
        "snapshot_id": provenance["snapshot_id"],
        "split_id": provenance["split_id"],
        "dataset_version_id": provenance.get("dataset_version_id"),
        "protocol": "explicit-json-binary-unseen-v1",
        "generation_k": 20,
        "source_sha256": source_hash(),
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


def plan_config(config):
    return {
        "grid": config.grid,
        "parameters": config.parameters,
        "seeds": list(config.seeds),
        "stage": config.stage,
    }


def load_study(config, *, allow_plan_change=False):
    _, conditions = context(config)
    path = config.output / "study.json"
    from src.infrastructure.research.store import exists

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
    if not allow_plan_change and result.get("configuration") != plan_config(config):
        raise ValueError("探索範囲・seedが変わっています。searchを実行してから再選択してください")
    return result


def open_study(config, conditions):
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


def require_unlocked(config):
    if (config.output / "result.json").exists():
        saved = read_json(config.output / "study.json")
        if saved["conditions"]["source_sha256"] != source_hash():
            raise ValueError(
                "保存時とコードが異なります / 閲覧は可能 / 再評価は別のoutputを指定してください"
            )
    if (config.output / "locked-selection.json").exists():
        raise ValueError("固定済み設定は探索・再選択できません。別の実験として開始してください")
