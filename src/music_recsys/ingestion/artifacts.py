"""内容hashで保存物を識別し、Parquetの書込みと読戻しを検証する。"""

import errno
import hashlib
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from music_recsys.ingestion.types import (
    Artifact,
    Catalog,
    ValidationError,
    canonical_json,
    valid_text,
)

WRITER_OPTIONS = {
    "version": "2.6",
    "compression": "zstd",
    "compression_level": 3,
    "use_dictionary": False,
    "data_page_version": "2.0",
    "write_statistics": True,
    "row_group_size": 65536,
}


def file_hashes(path: Path, algorithm: str | None = None) -> tuple[str, str | None, int]:
    sha = hashlib.sha256()
    extra = hashlib.new(algorithm) if algorithm else None
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(8 * 1024 * 1024):
            sha.update(chunk)
            if extra:
                extra.update(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValidationError("input file changed during verification")
    return sha.hexdigest(), extra.hexdigest() if extra else None, after.st_size


def catalog_tables(dataset_key: str, catalog: Catalog):
    import pyarrow as pa

    tracks_schema = pa.schema(
        [
            pa.field("dataset_key", pa.string(), nullable=False),
            pa.field("source_id", pa.string(), nullable=False),
            pa.field("metadata_json", pa.string(), nullable=True),
        ]
    )
    users_schema = pa.schema(
        [
            pa.field("dataset_key", pa.string(), nullable=False),
            pa.field("source_id", pa.string(), nullable=False),
        ]
    )
    return {
        "tracks": pa.Table.from_pylist(
            [
                {
                    "dataset_key": dataset_key,
                    "source_id": t.source_id,
                    "metadata_json": canonical_json(t.metadata) if t.metadata is not None else None,
                }
                for t in catalog.tracks
            ],
            schema=tracks_schema,
        ),
        "users": pa.Table.from_pylist(
            [{"dataset_key": dataset_key, "source_id": u.source_id} for u in catalog.users],
            schema=users_schema,
        ),
    }


class ArtifactStore:
    def __init__(self, root: Path, dataset_key: str):
        if not valid_text(dataset_key):
            raise ValidationError("invalid dataset key")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.dataset_key = dataset_key
        self.namespace = "dataset-" + quote(dataset_key, safe="")

    def put(
        self,
        source: Path,
        *,
        format: str,
        row_count: int | None = None,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        checksum_algorithm: str | None = None,
        expected_checksum: str | None = None,
    ) -> Artifact:
        if not re.fullmatch(r"[a-z0-9_]+", format) or checksum_algorithm not in (
            None,
            "md5",
            "sha256",
        ):
            raise ValidationError("unsupported format or checksum algorithm")
        if (checksum_algorithm is None) != (expected_checksum is None):
            raise ValidationError("checksum algorithm and value must both be specified")
        sha, checksum, size = file_hashes(Path(source), checksum_algorithm)
        if (
            (expected_sha256 is not None and sha != expected_sha256)
            or (expected_size is not None and size != expected_size)
            or (expected_checksum is not None and checksum != expected_checksum)
        ):
            raise ValidationError("source checksum or byte size mismatch")
        if format == "parquet":
            import pyarrow.parquet as pq

            actual_rows = pq.ParquetFile(source).metadata.num_rows
            if row_count is not None and row_count != actual_rows:
                raise ValidationError("Parquet row count mismatch")
            row_count = actual_rows
        key = f"{self.namespace}/{sha}.{format}"
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.parent.resolve().is_relative_to(self.root):
            raise ValidationError("artifact path escapes storage root")
        try:
            os.link(source, destination)
        except FileExistsError:
            pass
        except OSError as error:
            if error.errno not in (errno.EXDEV, errno.ENOTSUP):
                raise
            with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
                copied = Path(directory) / "copy"
                shutil.copyfile(source, copied)
                if file_hashes(copied)[0] != sha:
                    raise ValidationError("source changed during copy")
                try:
                    os.link(copied, destination)
                except FileExistsError:
                    pass
        artifact = Artifact(
            key, destination, format, sha, size, row_count, datetime.now(timezone.utc)
        )
        self.verify(artifact)
        return artifact

    def verify(self, artifact: Artifact) -> None:
        expected_key = f"{self.namespace}/{artifact.sha256}.{artifact.format}"
        if (
            artifact.storage_key != expected_key
            or artifact.path != self.root / expected_key
            or artifact.path.is_symlink()
            or not artifact.path.resolve().is_relative_to(self.root)
        ):
            raise ValidationError("artifact location mismatch")
        sha, _, size = file_hashes(artifact.path)
        if sha != artifact.sha256 or size != artifact.byte_size:
            raise ValidationError("stored artifact checksum mismatch")
        if artifact.format == "parquet":
            import pyarrow.parquet as pq

            if pq.ParquetFile(artifact.path).metadata.num_rows != artifact.row_count:
                raise ValidationError("stored Parquet row count mismatch")

    def write_catalog(self, catalog: Catalog) -> dict[str, Artifact]:
        import pyarrow.parquet as pq

        artifacts = {}
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            for role, table in catalog_tables(self.dataset_key, catalog).items():
                path = Path(directory) / f"{role}.parquet"
                pq.write_table(table, path, **WRITER_OPTIONS)
                artifacts[role] = self.put(path, format="parquet", row_count=table.num_rows)
        self.validate_catalog_files(artifacts, catalog)
        return artifacts

    def validate_catalog_files(self, artifacts: dict[str, Artifact], catalog: Catalog) -> None:
        import pyarrow.parquet as pq

        if set(artifacts) != {"tracks", "users"}:
            raise ValidationError("catalog requires tracks and users artifacts")
        for role, expected in catalog_tables(self.dataset_key, catalog).items():
            self.verify(artifacts[role])
            actual = pq.read_table(artifacts[role].path)
            if not actual.schema.equals(expected.schema, check_metadata=True) or not actual.equals(
                expected
            ):
                raise ValidationError("Parquet content or ID correspondence mismatch")
