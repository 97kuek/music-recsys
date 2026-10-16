"""許諾記録とID対応が揃ったローカル音源だけ、新規アプリbundleへ登録する。"""

import argparse
import csv
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from music_recsys.listening_data import file_sha256
from music_recsys.webapp.audit import AUDIO


def prepare(config_path, metadata_path, audio_root, permission_path, output):
    if output.exists():
        raise FileExistsError(output)
    permission = json.loads(permission_path.read_text())
    expiry = datetime.fromisoformat(permission["expires_at"])
    if (
        permission.get("web_preview") is not True
        or permission.get("access_scope") != "restricted-study"
        or not permission.get("evidence_ref")
        or expiry.utcoffset() is None
        or expiry <= datetime.now(UTC)
    ):
        raise ValueError("explicit current restricted-study web permission required")
    allowed = permission["item_ids"]
    if not isinstance(allowed, list) or not allowed or len(set(allowed)) != len(allowed):
        raise ValueError("permission must enumerate unique item IDs")
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
    if len(by_id) != len(metadata) or not set(allowed).issubset(known & set(by_id)):
        raise ValueError("unknown or duplicate metadata / permission IDs")
    root = audio_root.resolve()
    matches = {}
    for path in root.rglob("*"):
        if path.suffix.lower() not in AUDIO or path.stem not in allowed or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root) or path.stem in matches:
            raise ValueError("ambiguous or out-of-root audio")
        matches[path.stem] = path
    if set(matches) != set(allowed):
        raise ValueError("every permitted ID requires exactly one audio file")
    for item in allowed:
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
    args = parser.parse_args()
    print(prepare(args.config, args.metadata, args.audio_root, args.permission, args.output))


if __name__ == "__main__":
    main()
