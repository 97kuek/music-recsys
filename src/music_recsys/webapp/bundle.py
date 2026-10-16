"""Macへ持ち出す配信専用bundle。学習入力・ログ・認証情報を含めない。"""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from music_recsys.listening_data import file_sha256
from music_recsys.model_artifacts import load_model
from music_recsys.webapp.runtime import Runtime
from music_recsys.webapp.study import StudyPlan


def package(config_path, output, *, study_path=None):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    config = json.loads(config_path.read_text())
    if config["mode"] not in {"demo", "music4all"}:
        raise ValueError("portable bundle currently supports demo and music4all")
    plan = StudyPlan.read(study_path) if study_path else None
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".bundle-", dir=output.parent))
    try:
        result = {key: config[key] for key in ("mode", "selection", "catalog", "models", "audio")}
        result["models"] = []
        for i, entry in enumerate(config["models"]):
            source = config_path.parent / entry["path"]
            load_model(source, expected_manifest_sha256=entry["sha256"])
            manifest = json.loads((source / "manifest.json").read_text())
            target = stage / "models" / str(i)
            target.mkdir(parents=True)
            for name in ["manifest.json", *(f"{n}.npy" for n in manifest["files"])]:
                shutil.copyfile(source / name, target / name)
            result["models"].append(
                {
                    "path": f"models/{i}",
                    "sha256": entry["sha256"],
                    "als_fold_in": entry.get("als_fold_in", False),
                }
            )
        catalog = config_path.parent / config["catalog"]["path"]
        if file_sha256(catalog) != config["catalog"]["sha256"]:
            raise ValueError("catalog checksum mismatch")
        shutil.copyfile(catalog, stage / "catalog.json")
        result["catalog"] = {"path": "catalog.json", "sha256": file_sha256(catalog)}
        result["audio"] = {}
        for i, (item, meta) in enumerate(config.get("audio", {}).items()):
            source = (config_path.parent / meta["path"]).resolve()
            if (
                not source.is_relative_to(config_path.parent)
                or file_sha256(source) != meta["sha256"]
            ):
                raise ValueError("invalid audio file")
            relative = f"audio/{i}{source.suffix.lower()}"
            (stage / "audio").mkdir(exist_ok=True)
            shutil.copyfile(source, stage / relative)
            result["audio"][item] = {**meta, "path": relative}
        if config.get("media_permission_sha256"):
            permission = config_path.parent / "permission.json"
            if file_sha256(permission) != config["media_permission_sha256"]:
                raise ValueError("permission checksum mismatch")
            shutil.copyfile(permission, stage / "permission.json")
            result["media_permission_sha256"] = config["media_permission_sha256"]
        license_path = config_path.parent / "LICENSE.txt"
        if config["mode"] == "demo" and license_path.is_file():
            shutil.copyfile(license_path, stage / "LICENSE.txt")
        (stage / "config.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if plan:
            (stage / "study.json").write_text(plan.model_dump_json(indent=2))
        # コピー後に検証。検証用SQLiteはbundleへ混入させない。
        with tempfile.TemporaryDirectory() as check:
            Runtime(stage / "config.json", database=Path(check) / "check.sqlite", study=plan)
        (stage / "transfer.json").write_text(
            json.dumps(
                {
                    "format": "music-recsys-transfer-v1",
                    "source_config_sha256": file_sha256(config_path),
                    "files": {
                        str(p.relative_to(stage)): file_sha256(p)
                        for p in sorted(stage.rglob("*"))
                        if p.is_file()
                    },
                },
                indent=2,
            )
        )
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage)
        raise
    return output / "config.json"


def verify(directory):
    directory = Path(directory).resolve()
    record = json.loads((directory / "transfer.json").read_text())
    if record.get("format") not in {"music-recsys-transfer-v1", "otolab-transfer-v1"}:
        raise ValueError("unsupported transfer format")
    actual = {
        str(p.relative_to(directory))
        for p in directory.rglob("*")
        if p.is_file() and p != directory / "transfer.json"
    }
    if actual != set(record["files"]):
        raise ValueError("transfer file set mismatch")
    for name, digest in record["files"].items():
        path = directory / name
        if not path.resolve().is_relative_to(directory) or path.is_symlink():
            raise ValueError("unsafe transfer path")
        if file_sha256(path) != digest:
            raise ValueError("transfer checksum mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--study", type=Path)
    args = parser.parse_args()
    print(package(args.config, args.output, study_path=args.study))


if __name__ == "__main__":
    main()
