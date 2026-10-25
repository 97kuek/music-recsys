"""検証済みMusic4All原版の表示情報を既存カタログのID順に取り出す。"""

import csv
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from src.domain.datasets import canonical_json, valid_text
from src.infrastructure.datasets.listening_data import file_sha256


def prepare_metadata(source, config_path, output):
    """元文字列を保持するCSV/Parquetと出典manifestを新しい保存先へ公開する。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    source, config_path, output = Path(source).resolve(), Path(config_path).resolve(), Path(output)
    if output.exists():
        raise FileExistsError(output)
    manifest_path = source / "source-manifest.json"
    source_manifest_hash = file_sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("format") != "music4all-original-source-verification-v1"
        or manifest.get("status") != "verified"
    ):
        raise ValueError("verified Music4All original source manifest required")
    information = source / "id_information.csv"
    source_ref = manifest["files"][information.name]
    information_hash = file_sha256(information)
    if (
        information_hash != source_ref["sha256"]
        or information.stat().st_size != source_ref["bytes"]
    ):
        raise ValueError("source metadata checksum / size mismatch")

    config_hash = file_sha256(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("mode") != "music4all":
        raise ValueError("Music4All app configuration required")
    catalog_path = (config_path.parent / config["catalog"]["path"]).resolve()
    catalog_hash = file_sha256(catalog_path)
    if catalog_hash != config["catalog"]["sha256"]:
        raise ValueError("catalog checksum mismatch")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    items = [row["item_id"] for row in catalog]
    if not items or any(not valid_text(item) for item in items) or len(set(items)) != len(items):
        raise ValueError("catalog requires nonempty unique item IDs")
    selected = set(items)
    seen, by_id = set(), {}
    with information.open(encoding="utf-8", errors="strict", newline="") as stream:
        reader = csv.reader(stream, delimiter="\t", quotechar='"', doublequote=True, strict=True)
        if next(reader, None) != ["id", "artist", "song", "album_name"]:
            raise ValueError("unexpected Music4All metadata header")
        for row in reader:
            if len(row) != 4 or any(not valid_text(value) for value in row):
                raise ValueError("invalid Music4All metadata row")
            item, artist, title, album = row
            if item in seen:
                raise ValueError("duplicate Music4All metadata ID")
            seen.add(item)
            if item in selected:
                if "|" in artist:
                    raise ValueError("artist contains ambiguous online_media separator")
                by_id[item] = dict(item_id=item, title=title, artists=artist, album_name=album)
    if len(seen) != source_ref["rows"]:
        raise ValueError("source metadata row count mismatch")
    if missing := selected - by_id.keys():
        raise ValueError(f"metadata missing for {len(missing)} catalog items")
    rows = [by_id[item] for item in items]
    schema = pa.schema([(name, pa.string()) for name in rows[0]])

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".metadata-", dir=output.parent))
    try:
        with (stage / "tracks.csv").open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=schema.names, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), stage / "tracks.parquet")
        inputs = {
            "source_manifest": (manifest_path, source_manifest_hash),
            "information": (information, information_hash),
            "app_config": (config_path, config_hash),
            "catalog": (catalog_path, catalog_hash),
        }
        if any(file_sha256(path) != digest for path, digest in inputs.values()):
            raise ValueError("metadata input changed during preparation")
        result = {
            "format": "music4all-display-metadata-v1",
            "status": "succeeded",
            "created_at": datetime.now(UTC).isoformat(),
            "processor_sha256": file_sha256(Path(__file__)),
            "pyarrow_version": pa.__version__,
            "sources": {
                role: {"path": str(path), "sha256": digest}
                for role, (path, digest) in inputs.items()
            },
            "source_rows": len(seen),
            "catalog_items": len(items),
            "matched_items": len(rows),
            "missing_items": 0,
            "matching": "exact case-sensitive Music4All ID; original catalog row order",
            "columns": {
                "item_id": "id_information.id",
                "title": "id_information.song",
                "artists": "id_information.artist; one unchanged cell, no artist splitting",
                "album_name": "id_information.album_name",
            },
            "normalization": "none; preserve parsed strings, quotes and missing-value candidates",
            "scope": "display metadata only; no training, evaluation, model selection or playback permission",
            "files": {
                name: {
                    "sha256": file_sha256(stage / name),
                    "bytes": (stage / name).stat().st_size,
                    "rows": len(rows),
                }
                for name in ("tracks.csv", "tracks.parquet")
            },
        }
        (stage / "manifest.json").write_text(canonical_json(result) + "\n", encoding="utf-8")
        if output.exists():
            raise FileExistsError(output)
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage)
        raise
    return output / "manifest.json"
