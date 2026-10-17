"""Macへ持ち出す配信専用bundle。学習入力・ログ・認証情報を含めない。"""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from src.bootstrap import create_runtime
from src.infrastructure.config.study import read_study_plan
from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.models.model_artifacts import load_model


def inventory(config_path):
    """転送対象の実測容量。生成JSON・Docker image・stateの容量は含めない。"""
    path = Path(config_path).resolve()
    config = json.loads(path.read_text())
    if config["mode"] not in {"demo", "music4all"}:
        raise ValueError("portable bundle currently supports demo and music4all")
    sizes = {"models": 0, "catalog": 0, "audio": 0, "permission": 0, "license": 0}
    for entry in config["models"]:
        source = path.parent / entry["path"]
        load_model(source, expected_manifest_sha256=entry["sha256"])
        manifest = json.loads((source / "manifest.json").read_text())
        sizes["models"] += sum(
            (source / name).stat().st_size
            for name in ["manifest.json", *(f"{n}.npy" for n in manifest["files"])]
        )
    files = [("catalog", path.parent / config["catalog"]["path"], config["catalog"]["sha256"])]
    for entry in config.get("audio", {}).values():
        source = (path.parent / entry["path"]).resolve()
        if not source.is_relative_to(path.parent):
            raise ValueError("invalid audio file")
        files.append(("audio", source, entry["sha256"]))
    if config.get("media_permission_sha256"):
        files.append(
            ("permission", path.parent / "permission.json", config["media_permission_sha256"])
        )
    for role, source, digest in files:
        if file_sha256(source) != digest:
            raise ValueError(f"{role} checksum mismatch")
        sizes[role] += source.stat().st_size
    license_path = path.parent / "LICENSE.txt"
    if config["mode"] == "demo" and license_path.is_file():
        sizes["license"] = license_path.stat().st_size
    return {
        "source_config_sha256": file_sha256(path),
        "model_count": len(config["models"]),
        "audio_count": len(config.get("audio", {})),
        "bytes": sizes,
        "payload_bytes": sum(sizes.values()),
        "excludes": ["generated JSON", "Docker image/build cache", "source archive", "state/logs"],
    }


def package(config_path, output, *, study_path=None, max_bytes=None):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    if max_bytes is not None and (isinstance(max_bytes, bool) or max_bytes <= 0):
        raise ValueError("max_bytes must be positive")
    size = inventory(config_path)["payload_bytes"]
    if max_bytes is not None and size > max_bytes:
        raise ValueError("bundle exceeds max_bytes; reduce the explicit study catalog or models")
    config = json.loads(config_path.read_text())
    if config["mode"] not in {"demo", "music4all"}:
        raise ValueError("portable bundle currently supports demo and music4all")
    plan = read_study_plan(study_path) if study_path else None
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < size:
        raise ValueError("insufficient free disk for bundle payload")
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
            (stage / "study.json").write_text(
                json.dumps(plan.model_dump(), ensure_ascii=False, indent=2)
            )
        # コピー後に検証。検証用SQLiteはbundleへ混入させない。
        with tempfile.TemporaryDirectory() as check:
            create_runtime(stage / "config.json", database=Path(check) / "check.sqlite", study=plan)
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
        total = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
        if max_bytes is not None and total > max_bytes:
            raise ValueError("bundle exceeds max_bytes including generated JSON")
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
    parser.add_argument("--output", type=Path)
    parser.add_argument("--study", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="容量とhashを確認、コピーしない")
    parser.add_argument("--max-bytes", type=int, help="生成JSONを含むbundleの容量上限")
    args = parser.parse_args()
    if args.max_bytes is not None and args.max_bytes <= 0:
        parser.error("--max-bytes must be positive")
    if args.dry_run:
        report = inventory(args.config)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.max_bytes is not None and report["payload_bytes"] > args.max_bytes:
            parser.exit(1, "bundle payload exceeds --max-bytes\n")
    elif args.output is None:
        parser.error("--output is required unless --dry-run is used")
    else:
        print(package(args.config, args.output, study_path=args.study, max_bytes=args.max_bytes))


if __name__ == "__main__":
    main()
