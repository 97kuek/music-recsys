"""許諾記録とID対応が揃ったローカル音源だけ、新規アプリbundleへ登録する。"""

import argparse
import csv
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from src.infrastructure.datasets.listening_data import file_sha256
from src.infrastructure.media.playback import parse_expiry
from src.interfaces.cli.online_audit import AUDIO


def prepare(config_path, metadata_path, audio_root, permission_path, output, *, item_ids=None):
    if output.exists():
        raise FileExistsError(output)
    permission = json.loads(permission_path.read_text())
    expiry = parse_expiry(permission["expires_at"])
    if (
        permission.get("web_preview") is not True
        or permission.get("access_scope") != "restricted-study"
        or not permission.get("evidence_ref")
        or (expiry is not None and expiry <= datetime.now(UTC))
    ):
        raise ValueError("explicit current restricted-study web permission required")
    allowed = permission["item_ids"]
    if not isinstance(allowed, list) or not allowed or len(set(allowed)) != len(allowed):
        raise ValueError("permission must enumerate unique item IDs")
    selected = allowed if item_ids is None else item_ids
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(item, str) or not item for item in selected)
        or len(set(selected)) != len(selected)
        or not set(selected).issubset(allowed)
    ):
        raise ValueError("selected item IDs must be unique and permitted")
    selected = set(selected)
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text())
    if config["mode"] != "music4all":
        raise ValueError("Music4All config required")
    catalog_path = config_path.parent / config["catalog"]["path"]
    if file_sha256(catalog_path) != config["catalog"]["sha256"]:
        raise ValueError("catalog checksum mismatch")
    catalog = json.loads(catalog_path.read_text())
    known = {r["item_id"] for r in catalog}
    with metadata_path.open(newline="") as f:
        metadata = list(csv.DictReader(f))
    by_id = {r["item_id"]: r for r in metadata}
    if len(by_id) != len(metadata) or not selected.issubset(known & set(by_id)):
        raise ValueError("unknown or duplicate metadata / permission IDs")
    root = audio_root.resolve()
    matches = {}
    for path in root.rglob("*"):
        if path.suffix.lower() not in AUDIO or path.stem not in selected or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root) or path.stem in matches:
            raise ValueError("ambiguous or out-of-root audio")
        matches[path.stem] = path
    if set(matches) != selected:
        raise ValueError("every permitted ID requires exactly one audio file")
    for item in selected:
        if not by_id[item]["title"].strip() or not by_id[item]["artists"].strip():
            raise ValueError("title and artists required")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".media-", dir=output.parent))
    try:
        (stage / "audio").mkdir()
        audio = {}
        for item, path in matches.items():
            target = stage / "audio" / path.name
            shutil.copyfile(path, target)
            audio[item] = {
                "path": str(target.relative_to(stage)),
                "sha256": file_sha256(target),
                "license_ref": permission["evidence_ref"],
                "expires_at": permission["expires_at"],
                "sample": False,
            }
        # 同じ固定カタログを初期選択と全比較条件の推薦候補へ適用する
        catalog = [row for row in catalog if row["item_id"] in selected]
        for row in catalog:
            if row["item_id"] in by_id:
                meta = by_id[row["item_id"]]
                row.update(
                    title=meta["title"].strip(),
                    artists=[a.strip() for a in meta["artists"].split("|") if a.strip()],
                )
        for model in config["models"]:
            model["path"] = str((config_path.parent / model["path"]).resolve())
        (stage / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False))
        config.update(
            audio=audio,
            catalog={"path": "catalog.json", "sha256": file_sha256(stage / "catalog.json")},
            media_permission_sha256=file_sha256(permission_path),
        )
        shutil.copyfile(permission_path, stage / "permission.json")
        (stage / "config.json").write_text(json.dumps(config, ensure_ascii=False))
        stage.rename(output)
    except Exception:
        shutil.rmtree(stage)
        raise
    return output / "config.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "metadata", "audio-root", "permission", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--items", type=Path, help="実験で使う楽曲IDのJSON配列、省略時は許諾ID全件")
    args = parser.parse_args()
    print(
        prepare(
            args.config,
            args.metadata,
            args.audio_root,
            args.permission,
            args.output,
            item_ids=json.loads(args.items.read_text()) if args.items else None,
        )
    )


if __name__ == "__main__":
    main()
