"""取り込み境界で使う共通形式と、DBに依存しない検証。"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from music_recsys.ingestion.artifacts import ArtifactStore


class ValidationError(ValueError):
    """入力または保存物が取り込み契約を満たさない。"""


class ImportFailed(RuntimeError):
    def __init__(self, run_id: UUID, error_type: str):
        self.run_id, self.error_type = run_id, error_type
        super().__init__(f"import failed: {error_type}; run_id={run_id}")


def valid_text(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def canonical_json(value: object) -> str:
    """recsys_canonical_jsonと同じUTF-8・キー順・数値表記で符号化する。"""
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValidationError("non-finite JSON number")
        return "0" if value == 0 else format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, str):
        if "\0" in value:
            raise ValidationError("JSON contains a null character")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValidationError("JSON is not valid UTF-8") from None
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValidationError("JSON object keys must be strings")
        return (
            "{"
            + ",".join(canonical_json(k) + ":" + canonical_json(value[k]) for k in sorted(value))
            + "}"
        )
    raise ValidationError("unsupported JSON value")


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def source_version(root: Path, files: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0")
        digest.update(hashlib.sha256((root / name).read_bytes()).digest())
    return "1:" + digest.hexdigest()


@dataclass(frozen=True)
class SourceSpec:
    name: str
    path: Path
    source_url: str
    byte_size: int
    checksum_algorithm: str
    checksum: str
    format: str = "bz2"


@dataclass(frozen=True)
class DatasetRelease:
    key: str
    version_label: str
    source_ref: str
    manifest: dict
    sources: tuple[SourceSpec, ...]


@dataclass(frozen=True)
class Artifact:
    storage_key: str
    path: Path
    format: str
    sha256: str
    byte_size: int
    row_count: int | None
    verified_at: datetime


@dataclass(frozen=True)
class AdapterInput:
    config: dict
    files: dict[str, Path]
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True)
class TrackRecord:
    source_id: str
    metadata: dict | None


@dataclass(frozen=True)
class UserRecord:
    source_id: str


@dataclass(frozen=True)
class Catalog:
    tracks: tuple[TrackRecord, ...]
    users: tuple[UserRecord, ...]
    stats: dict[str, int]


def validate_catalog(catalog: Catalog) -> Catalog:
    def identities(records):
        ids = [r.source_id for r in records]
        if not all(valid_text(v) for v in ids) or len(ids) != len(set(ids)):
            raise ValidationError("catalog IDs are blank, invalid or duplicated")

    identities(catalog.tracks)
    identities(catalog.users)
    tracks = []
    for t in sorted(catalog.tracks, key=lambda r: r.source_id):
        payload = t.metadata
        if payload is not None:
            if not isinstance(payload, dict) or not isinstance(payload.get("tags"), dict):
                raise ValidationError("metadata requires a tags object")
            if any(
                not valid_text(tag) or type(weight) is not int or not 1 <= weight <= 100
                for tag, weight in payload["tags"].items()
            ):
                raise ValidationError("invalid tag or weight")
            payload = json.loads(canonical_json(payload))
        tracks.append(TrackRecord(t.source_id, payload))
    if any(not valid_text(k) or type(v) is not int or v < 0 for k, v in catalog.stats.items()):
        raise ValidationError("stats must contain only nonnegative aggregate counts")
    return Catalog(
        tuple(tracks), tuple(sorted(catalog.users, key=lambda r: r.source_id)), dict(catalog.stats)
    )


@dataclass(frozen=True)
class ImportResult:
    run_id: UUID
    snapshot_id: UUID
    tracks: int
    users: int
    reused: bool
    input_fingerprint: str
    content_fingerprint: str


class DatasetAdapter(Protocol):
    name: str
    version: str
    release: DatasetRelease

    def prepare(self, store: "ArtifactStore", sources: dict[str, Artifact]) -> AdapterInput: ...

    def load(self, inputs: AdapterInput) -> Catalog: ...
