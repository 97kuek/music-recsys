"""共通形式とMusic4Allの実ヘッダー・literal解析の境界を検証する。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_tag_literal_preserves_empty_dictionary():
    from music_recsys.adapters.music4all import parse_tags

    assert parse_tags("{}") == {"tags": {}}
    assert parse_tags("{'音楽': 100, 'rock': 1}") == {"tags": {"rock": 1, "音楽": 100}}


@pytest.mark.parametrize(
    "literal",
    [
        "__import__('os').getcwd()",
        "['rock']",
        "{'rock': True}",
        "{'rock': 1.0}",
        "{'rock': 0}",
        "{'rock': 101}",
        "{' ': 1}",
        "{1: 90}",
        "{'rock': 1, 'rock': 99}",
    ],
)
def test_invalid_tag_literals_are_rejected(literal):
    from music_recsys.adapters.music4all import parse_tags
    from music_recsys.ingestion.types import ValidationError

    with pytest.raises(ValidationError):
        parse_tags(literal)


def test_common_catalog_preserves_ids_and_missing_metadata():
    from music_recsys.ingestion.types import Catalog, TrackRecord, UserRecord, validate_catalog

    result = validate_catalog(
        Catalog(
            tracks=(TrackRecord("001", None), TrackRecord("01", {"tags": {}})),
            users=(UserRecord("0002"), UserRecord("2")),
            stats={},
        )
    )
    assert [t.source_id for t in result.tracks] == ["001", "01"]
    assert [t.metadata for t in result.tracks] == [None, {"tags": {}}]


@pytest.mark.parametrize("source_id", ["", " ", "\t\n", "\u00a0", "bad\x00id"])
def test_common_catalog_rejects_invalid_ids(source_id):
    from music_recsys.ingestion.types import Catalog, TrackRecord, ValidationError, validate_catalog

    with pytest.raises(ValidationError):
        validate_catalog(Catalog((TrackRecord(source_id, None),), (), {}))


def test_common_catalog_rejects_duplicate_identity():
    from music_recsys.ingestion.types import Catalog, TrackRecord, ValidationError, validate_catalog

    with pytest.raises(ValidationError):
        validate_catalog(Catalog((TrackRecord("x", None), TrackRecord("x", None)), (), {}))


def test_json_normalization_matches_database_numeric_rules():
    from music_recsys.ingestion.types import canonical_json

    assert canonical_json({"z": [True, None, 1.00, 1e-3, -0.0], "a": "音楽"}) == (
        '{"a":"音楽","z":[true,null,1,0.001,0]}'
    )


def adapter_for(files):
    from music_recsys.adapters.music4all import Music4AllAdapter

    return Music4AllAdapter(
        record_path=files["record"],
        tags_path=files["tags"],
        events_path=files["events"],
        event_cache=files["cache"],
        version_label="v2",
        track_limit=3,
        user_limit=2,
    )


def prepared_adapter(files, root):
    from music_recsys.ingestion.artifacts import ArtifactStore

    adapter = adapter_for(files)
    store = ArtifactStore(root, adapter.release.key)
    sources = {
        s.name: store.put(
            s.path,
            format=s.format,
            expected_size=s.byte_size,
            checksum_algorithm=s.checksum_algorithm,
            expected_checksum=s.checksum,
        )
        for s in adapter.release.sources
    }
    return adapter, adapter.prepare(store, sources)


def test_adapter_uses_lexicographic_ids_and_preserves_metadata_availability(
    music4all_factory, tmp_path
):
    adapter, inputs = prepared_adapter(music4all_factory(), tmp_path / "store")
    catalog = adapter.load(inputs)
    assert [t.source_id for t in catalog.tracks] == ["01", "02", "03"]
    assert [t.metadata for t in catalog.tracks] == [{"tags": {}}, {"tags": {"rock": 80}}, None]
    assert [u.source_id for u in catalog.users] == ["0002", "0010"]
    assert catalog.stats["tag_duplicate_rows"] == 1
    assert catalog.stats["missing_metadata"] == 1
    assert catalog.stats["empty_tags"] == 1


def test_adapter_rejects_conflicting_duplicate_tag_rows(music4all_factory, tmp_path):
    from music_recsys.ingestion.types import ValidationError

    files = music4all_factory(tag_rows=[("02", "{'rock': 80}"), ("02", "{'rock': 90}")])
    adapter, inputs = prepared_adapter(files, tmp_path / "store")
    with pytest.raises(ValidationError):
        adapter.load(inputs)


@pytest.mark.parametrize("checksum", [None, "", "x" * 64, "A" * 64, 12])
def test_adapter_requires_valid_cache_manifest_checksum(music4all_factory, tmp_path, checksum):
    from music_recsys.ingestion.types import ValidationError

    files = music4all_factory()
    manifest_path = files["cache"].with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text())
    manifest["parquet_sha256"] = checksum
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        prepared_adapter(files, tmp_path / "store")


@pytest.mark.parametrize("invalid_id", ["z\x00bad", "\u00a0"])
@pytest.mark.parametrize("column", ["user", "track"])
def test_adapter_rejects_invalid_event_ids_outside_subset(
    music4all_factory, tmp_path, invalid_id, column
):
    from music_recsys.ingestion.types import ValidationError

    user, track = (invalid_id, "zz") if column == "user" else ("zz", invalid_id)
    events = (
        "user_id\ttrack_id\ttimestamp\n"
        "0002\t01\t2020-01-01 00:00:00\n"
        "0010\t02\t2020-01-02 00:00:00\n"
        "2\t03\t2020-01-03 00:00:00\n"
        f"{user}\t{track}\t2020-01-04 00:00:00\n"
    ).encode()
    adapter, inputs = prepared_adapter(music4all_factory(events_data=events), tmp_path / "store")
    with pytest.raises(ValidationError):
        adapter.load(inputs)


def test_source_checksum_failure_does_not_save_an_artifact(tmp_path):
    from music_recsys.ingestion.artifacts import ArtifactStore
    from music_recsys.ingestion.types import ValidationError

    source = tmp_path / "raw"
    source.write_bytes(b"source")
    store = ArtifactStore(tmp_path / "store", "test")
    with pytest.raises(ValidationError):
        store.put(source, format="bz2", checksum_algorithm="md5", expected_checksum="0" * 32)
    assert not list(store.root.rglob("*.bz2"))


def test_existing_artifact_is_reused_and_corruption_is_rejected(tmp_path):
    from music_recsys.ingestion.artifacts import ArtifactStore
    from music_recsys.ingestion.types import ValidationError

    source = tmp_path / "raw"
    source.write_bytes(b"source")
    store = ArtifactStore(tmp_path / "store", "test")
    a = store.put(source, format="bz2")
    b = store.put(source, format="bz2")
    assert a.storage_key == b.storage_key
    assert len(list(store.root.rglob("*.bz2"))) == 1
    a.path.write_bytes(b"modified")
    with pytest.raises(ValidationError):
        store.verify(a)


def test_parquet_output_is_deterministic_and_reads_back_exact_catalog(tmp_path):
    pytest.importorskip("pyarrow", reason="install the data dependency group")
    from music_recsys.ingestion.artifacts import ArtifactStore
    from music_recsys.ingestion.types import Catalog, TrackRecord, UserRecord, validate_catalog

    catalog = validate_catalog(
        Catalog(
            (TrackRecord("01", None), TrackRecord("001", {"tags": {}})), (UserRecord("0002"),), {}
        )
    )
    a = ArtifactStore(tmp_path / "a", "test")
    b = ArtifactStore(tmp_path / "b", "test")
    first = a.write_catalog(catalog)
    second = b.write_catalog(catalog)
    assert {k: v.sha256 for k, v in first.items()} == {k: v.sha256 for k, v in second.items()}
    a.validate_catalog_files(first, catalog)
    b.validate_catalog_files(second, catalog)


def test_import_cli_help_does_not_require_database_credentials():
    result = subprocess.run(
        [sys.executable, "-m", "music_recsys.ingestion.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--event-cache" in result.stdout


def test_import_cli_does_not_echo_exception_body_or_private_paths(tmp_path):
    missing = tmp_path / "do-not-echo-private-path.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "music_recsys.ingestion.cli",
            "--record",
            str(missing),
            "--tags",
            "missing",
            "--events",
            "missing",
            "--event-cache",
            "missing",
            "--version-label",
            "v2",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "do-not-echo-private-path" not in result.stdout + result.stderr
    assert '"error_type": "FileNotFoundError"' in result.stderr


def test_import_cli_reports_missing_optional_dependencies_without_a_traceback():
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "music_recsys.ingestion.cli",
            "--record",
            "missing",
            "--tags",
            "missing",
            "--events",
            "missing",
            "--event-cache",
            "missing",
            "--version-label",
            "v2",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert '"error_type": "ModuleNotFoundError"' in result.stderr
