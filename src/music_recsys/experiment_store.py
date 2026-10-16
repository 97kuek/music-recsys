"""単一result.jsonへの保存と旧保存物の無損失移行。"""

import copy
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

FORMAT = "music-recsys-result-v1"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    os.replace(temp, path)


@lru_cache(maxsize=2)
def _cached(path, mtime, size):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_document(path):
    path = Path(path)
    stat = path.stat()
    return copy.deepcopy(_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size))


def read_value(path):
    text = str(path)
    if "#/" in text:
        name, pointer = text.split("#/", 1)
        path = Path(name)
        stat = path.stat()
        value = _cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        for key in pointer.split("/"):
            value = value[key]
        return copy.deepcopy(value)
    path = Path(path)
    if path.name == "study.json" and not path.exists() and (path.parent / "result.json").exists():
        return read_value(str(path.parent / "result.json") + "#/study")
    return json.loads(path.read_text(encoding="utf-8"))


def exists(path):
    if "#/" not in str(path):
        p = Path(path)
        return p.is_file() or (p.name == "study.json" and (p.parent / "result.json").is_file())
    try:
        read_value(path)
        return True
    except (OSError, KeyError, TypeError):
        return False


def value_hash(path):
    if "#/" in str(path):
        return hashlib.sha256(encoded(read_value(path))).hexdigest()
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_value(path, value):
    path = Path(path)
    packed = path.parent / "result.json"
    if path.name == "study.json" and not path.exists() and packed.exists():
        document = read_document(packed)
        if document.get("format") != FORMAT:
            raise ValueError("result.jsonの保存形式が異なります")
        document["study"] = value
        # 新しい試行中は古い集計値を公開しない
        document.pop("analysis", None)
        atomic_json(packed, document)
    else:
        atomic_json(path, value)


def compact(config, *, output=None, migrate=False):
    """呼出し側でstudy_lockを保持 / 原本hash検証後に公開・旧階層を削除。"""
    from music_recsys.evaluation.benchmark import _source_sha256

    source = config.output
    output = Path(output or source)
    same = output.resolve() == source.resolve()
    if not same:
        output.mkdir(parents=True, exist_ok=False)
    study = read_value(source / "study.json")
    document = {"format": FORMAT, "study": study, "trials": {}}
    previous_analysis = str(source / "result.json") + "#/analysis"
    if exists(previous_analysis):
        document["analysis"] = read_value(previous_analysis)
    final_path = source / "final-test.json"
    states = [study]
    final = read_value(final_path) if final_path.exists() else None
    if final:
        states.append(final)
    for state in states:
        for key, trial in state["trials"].items():
            payload = {}
            for role in ("report", "curves"):
                if role not in trial:
                    continue
                path = source / trial[role]
                if value_hash(path) != trial[role + "_sha256"]:
                    raise ValueError("保存済み試行のhashが一致しません")
                value = read_value(path)
                if hashlib.sha256(encoded(value)).hexdigest() != trial[role + "_sha256"]:
                    raise ValueError("無損失移行できないJSON形式です")
                payload[role] = value
                trial[role] = f"result.json#/trials/{key}/{role}"
            if payload:
                document["trials"][key] = payload
    config_path = output / "config.json"
    snapshot = read_value(config_path) if config_path.exists() else {}
    snapshot.update(
        source_config=str(config.path),
        resolved_config=json.loads(json.dumps(asdict(config), default=str)),
        actual_conditions=study["conditions"],
    )
    if migrate:
        snapshot["storage_migration"] = {
            "evaluation_source_sha256": study["conditions"]["source_sha256"],
            "migration_source_sha256": _source_sha256(),
            "evaluation_rerun": False,
        }
    # 元の実行設定も保存（移動前のパスなどを含む）
    legacy_config = source / "report/config.json"
    if legacy_config.exists():
        snapshot["original_execution_config"] = read_value(legacy_config)
    atomic_json(config_path, snapshot)
    atomic_json(output / "result.json", document)
    if final:
        atomic_json(output / "final-test.json", final)
    log_text = ""
    if (source / "run.log").exists():
        log_text = (source / "run.log").read_text(encoding="utf-8")
    for log in sorted((source / "logs").glob("*.log")):
        log_text += f"\n[{log.name}]\n" + log.read_text(encoding="utf-8")
    if log_text or not (output / "run.log").exists():
        (output / "run.log").write_text(log_text)
    if same:
        (source / "study.json").unlink(missing_ok=True)
        for name in ("trials", "logs", "report"):
            path = source / name
            if path.exists():
                shutil.rmtree(path)
    return document
