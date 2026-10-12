"""Music4All-Onionの公開ID・重み付きタグを共通形式へ変換する。"""

import ast
import bz2
import csv
import json
import re
from collections.abc import Callable
from pathlib import Path

from music_recsys.ingestion.artifacts import ArtifactStore
from music_recsys.ingestion.types import (
    AdapterInput,
    Artifact,
    Catalog,
    DatasetRelease,
    SourceSpec,
    TrackRecord,
    UserRecord,
    ValidationError,
    fingerprint,
    source_version,
    valid_text,
)


def parse_tags(value: str) -> dict:
    try:
        syntax = ast.parse(value, mode="eval").body
        tags = ast.literal_eval(syntax)
    except (SyntaxError, ValueError, TypeError, RecursionError):
        raise ValidationError("invalid tag dictionary literal") from None
    if (
        not isinstance(syntax, ast.Dict)
        or not isinstance(tags, dict)
        or len(syntax.keys) != len(tags)
    ):
        raise ValidationError("tags must be a dictionary with unique keys")
    if any(not valid_text(k) or type(v) is not int or not 1 <= v <= 100 for k, v in tags.items()):
        raise ValidationError("tag names or integer weights are invalid")
    return {"tags": dict(sorted(tags.items()))}


class Music4AllAdapter:
    name = "music4all-onion"

    def __init__(
        self,
        *,
        record_path: Path,
        tags_path: Path,
        events_path: Path,
        event_cache: Path,
        version_label: str,
        track_limit: int,
        user_limit: int,
        progress: Callable[[str], None] | None = None,
    ):
        if any(type(n) is not int or n <= 0 for n in (track_limit, user_limit)):
            raise ValidationError("subset limits must be positive integers")
        if not valid_text(version_label):
            raise ValidationError("version label is required")
        self.event_cache = Path(event_cache)
        self.track_limit, self.user_limit = track_limit, user_limit
        self.progress = progress or (lambda message: None)
        self.version = source_version(
            Path(__file__).resolve().parents[1], ("adapters/music4all.py", "music4all.py")
        )
        try:
            record = json.loads(Path(record_path).read_text(encoding="utf-8"))
            if type(record["id"]) is not int or record["id"] <= 0:
                raise ValidationError("invalid Zenodo record ID")
            descriptors, sources, seen = [], [], set()
            selected = {
                "id_tags_dict.tsv.bz2": Path(tags_path),
                "userid_trackid_timestamp.tsv.bz2": Path(events_path),
            }
            for f in sorted(record["files"], key=lambda f: f["key"]):
                name, size = f["key"], f["size"]
                algo, checksum = f["checksum"].split(":", 1)
                if not valid_text(name) or name in seen or type(size) is not int or size < 0:
                    raise ValidationError("invalid or duplicated source descriptor")
                seen.add(name)
                descriptors.append(
                    {
                        "name": name,
                        "byte_size": size,
                        "checksum": {"algorithm": algo, "value": checksum},
                    }
                )
                if name in selected:
                    sources.append(
                        SourceSpec(name, selected[name], f["links"]["self"], size, algo, checksum)
                    )
            if len(sources) != 2:
                raise ValidationError("record does not contain both required files")
            self.release = DatasetRelease(
                self.name,
                version_label,
                f"https://zenodo.org/records/{record['id']}",
                {"files": descriptors},
                tuple(sources),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, ValidationError):
                raise
            raise ValidationError("invalid Zenodo file record") from None

    def prepare(self, store: ArtifactStore, sources: dict[str, Artifact]) -> AdapterInput:
        import pyarrow as pa
        import pyarrow.parquet as pq

        self.progress("Verifying event cache and conversion manifest")
        manifest_path = self.event_cache.with_suffix(".manifest.json")
        manifest_artifact = store.put(manifest_path, format="json")
        manifest = json.loads(manifest_artifact.path.read_text(encoding="utf-8"))
        raw = sources["userid_trackid_timestamp.tsv.bz2"]
        required = {
            "format_version",
            "pyarrow_version",
            "source_sha256",
            "parquet_sha256",
            "source_bytes",
            "n_events",
            "timestamp_interpretation",
        }
        if (
            set(manifest) != required
            or type(manifest.get("format_version")) is not int
            or manifest.get("format_version") != 1
            or type(manifest.get("source_bytes")) is not int
            or type(manifest.get("n_events")) is not int
            or manifest.get("n_events", -1) < 0
            or manifest.get("source_sha256") != raw.sha256
            or manifest.get("source_bytes") != raw.byte_size
            or not valid_text(manifest.get("pyarrow_version"))
            or not isinstance(manifest.get("parquet_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest["parquet_sha256"]) is None
        ):
            raise ValidationError("event cache provenance mismatch")
        cached = store.put(
            self.event_cache,
            format="parquet",
            row_count=manifest["n_events"],
            expected_sha256=manifest["parquet_sha256"],
        )
        schema = pq.read_schema(cached.path)
        if (
            schema.names != ["user_id", "item_id", "event_time"]
            or schema.field("user_id").type != pa.string()
            or schema.field("item_id").type != pa.string()
            or not pa.types.is_timestamp(schema.field("event_time").type)
            or schema.field("event_time").type.tz is not None
        ):
            raise ValidationError("unexpected converted event schema")
        config = {
            "selection": "source_id_lexicographic",
            "track_limit": self.track_limit,
            "user_limit": self.user_limit,
            "seed": None,
            "event_cache": {
                "sha256": cached.sha256,
                "manifest_sha256": manifest_artifact.sha256,
                "conversion": manifest,
            },
        }
        return AdapterInput(
            config,
            {"events": cached.path, "tags": sources["id_tags_dict.tsv.bz2"].path},
            (cached, manifest_artifact),
        )

    def load(self, inputs: AdapterInput) -> Catalog:
        import duckdb

        self.progress("Selecting track and user IDs from event cache")
        with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as connection:
            path = str(inputs.files["events"])

            def select_ids(column: str, limit: int) -> list[str]:
                # 固定の列名のみ使用し、全量のdistinct IDを共通規則で検証する。
                ids = [
                    row[0]
                    for row in connection.execute(
                        f"SELECT DISTINCT {column} FROM read_parquet(?)", [path]
                    ).fetchall()
                ]
                if not all(valid_text(value) for value in ids):
                    raise ValidationError("event cache contains invalid IDs")
                return sorted(ids)[:limit]

            tracks = select_ids("item_id", inputs.config["track_limit"])
            users = select_ids("user_id", inputs.config["user_limit"])
        if len(tracks) != inputs.config["track_limit"] or len(users) != inputs.config["user_limit"]:
            raise ValidationError("dataset has fewer IDs than requested")
        selected, hashes, payloads = set(tracks), {}, {}
        rows, duplicates = 0, 0
        self.progress("Validating weighted tag rows")
        with bz2.open(inputs.files["tags"], "rt", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if reader.fieldnames != ["id", "(tag, weight)"]:
                raise ValidationError("unexpected tag columns")
            for row in reader:
                if set(row) != {"id", "(tag, weight)"} or not valid_text(row["id"]):
                    raise ValidationError("invalid tag row or source ID")
                payload = parse_tags(row["(tag, weight)"])
                checksum = fingerprint(payload)
                if row["id"] in hashes:
                    if hashes[row["id"]] != checksum:
                        raise ValidationError("conflicting duplicate track metadata")
                    duplicates += 1
                else:
                    hashes[row["id"]] = checksum
                if row["id"] in selected:
                    if row["id"] in payloads and payloads[row["id"]] != payload:
                        raise ValidationError("conflicting duplicate track metadata")
                    payloads[row["id"]] = payload
                rows += 1
                if rows % 10000 == 0:
                    self.progress(f"Validated {rows:,} tag rows")
        stats = {
            "tag_rows": rows,
            "tag_unique_ids": len(hashes),
            "tag_duplicate_rows": duplicates,
            "missing_metadata": len(selected - payloads.keys()),
            "empty_tags": sum(not p["tags"] for p in payloads.values()),
        }
        return Catalog(
            tuple(TrackRecord(t, payloads.get(t)) for t in tracks),
            tuple(UserRecord(u) for u in users),
            stats,
        )
