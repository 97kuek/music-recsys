"""共通形式とMusic4Allの実ヘッダー・literal解析の境界を検証する。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_tag_literal_preserves_empty_dictionary():
    from src.infrastructure.datasets.music4all_adapter import parse_tags

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
    from src.domain.datasets import ValidationError
    from src.infrastructure.datasets.music4all_adapter import parse_tags

    with pytest.raises(ValidationError):
        parse_tags(literal)


def test_common_catalog_preserves_ids_and_missing_metadata():
    from src.domain.datasets import Catalog, TrackRecord, UserRecord, validate_catalog

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
    from src.domain.datasets import Catalog, TrackRecord, ValidationError, validate_catalog

    with pytest.raises(ValidationError):
        validate_catalog(Catalog((TrackRecord(source_id, None),), (), {}))


def test_common_catalog_rejects_duplicate_identity():
    from src.domain.datasets import Catalog, TrackRecord, ValidationError, validate_catalog

    with pytest.raises(ValidationError):
        validate_catalog(Catalog((TrackRecord("x", None), TrackRecord("x", None)), (), {}))


def test_json_normalization_matches_database_numeric_rules():
    from src.domain.datasets import canonical_json

    assert canonical_json({"z": [True, None, 1.00, 1e-3, -0.0], "a": "音楽"}) == (
        '{"a":"音楽","z":[true,null,1,0.001,0]}'
    )


def adapter_for(files, **options):
    from src.infrastructure.datasets.music4all_adapter import Music4AllAdapter

    return Music4AllAdapter(
        record_path=files["record"],
        tags_path=files["tags"],
        events_path=files["events"],
        event_cache=files["cache"],
        version_label="v2",
        **{"track_limit": 3, "user_limit": 2, **options},
    )


def prepared_adapter(files, root, **options):
    from src.infrastructure.ingestion.artifacts import ArtifactStore

    adapter = adapter_for(files, **options)
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
    from src.domain.datasets import ValidationError

    files = music4all_factory(tag_rows=[("02", "{'rock': 80}"), ("02", "{'rock': 90}")])
    adapter, inputs = prepared_adapter(files, tmp_path / "store")
    with pytest.raises(ValidationError):
        adapter.load(inputs)


@pytest.mark.parametrize("checksum", [None, "", "x" * 64, "A" * 64, 12])
def test_adapter_requires_valid_cache_manifest_checksum(music4all_factory, tmp_path, checksum):
    from src.domain.datasets import ValidationError

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
    from src.domain.datasets import ValidationError

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
    from src.domain.datasets import ValidationError
    from src.infrastructure.ingestion.artifacts import ArtifactStore

    source = tmp_path / "raw"
    source.write_bytes(b"source")
    store = ArtifactStore(tmp_path / "store", "test")
    with pytest.raises(ValidationError):
        store.put(source, format="bz2", checksum_algorithm="md5", expected_checksum="0" * 32)
    assert not list(store.root.rglob("*.bz2"))


def test_existing_artifact_is_reused_and_corruption_is_rejected(tmp_path):
    from src.domain.datasets import ValidationError
    from src.infrastructure.ingestion.artifacts import ArtifactStore

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
    from src.domain.datasets import Catalog, TrackRecord, UserRecord, validate_catalog
    from src.infrastructure.ingestion.artifacts import ArtifactStore

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
        [sys.executable, "-m", "src.interfaces.cli.ingest", "--help"],
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
            "src.interfaces.cli.ingest",
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
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "src.interfaces.cli.ingest",
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


HISTORY_ROWS = [
    "0000\t01\t2020-01-05 00:00:00",  # future-only, even though first by ID
    "0001\t02\t2020-01-01 00:00:00",
    "0001\t99\t2020-01-05 00:00:00",
    "0001\t99\t2020-01-05 00:00:00",  # repeated play must survive
    "0002\t01\t2020-01-04 00:00:00",  # exactly at cutoff: cold
    "0003\t03\t2020-01-02 00:00:00",
    "0003\t04\t2020-01-06 00:00:00",
    "0004\t05\t2020-01-03 00:00:00",  # eligible without future outcomes
]


def history_source(factory, name="history", rows=None):
    return factory(
        name=name,
        events_data=(
            "user_id\ttrack_id\ttimestamp\n"
            + "\n".join(HISTORY_ROWS if rows is None else rows)
            + "\n"
        ).encode(),
    )


def test_history_selects_only_train_eligible_users_and_keeps_all_their_events(
    music4all_factory, tmp_path
):
    import pyarrow.parquet as pq

    adapter, inputs = prepared_adapter(
        history_source(music4all_factory),
        tmp_path / "store",
        selection="history",
        train_end="2020-01-04T00:00:00",
        track_limit=1,
    )
    catalog = adapter.load(inputs)
    assert [user.source_id for user in catalog.users] == ["0001", "0003"]
    assert [track.source_id for track in catalog.tracks] == ["02", "03", "04", "99"]
    events = pq.read_table(inputs.files["events"]).to_pydict()
    assert list(zip(events["user_id"], events["item_id"], strict=True)) == [
        ("0001", "02"),
        ("0001", "99"),
        ("0001", "99"),
        ("0003", "03"),
        ("0003", "04"),
    ]
    selected_artifact = next(a for a in inputs.artifacts if a.path == inputs.files["events"])
    assert inputs.config["history_events"] == {
        "storage_key": selected_artifact.storage_key,
        "sha256": selected_artifact.sha256,
        "row_count": 5,
    }
    assert inputs.config["selection_mode"] == "history"
    assert inputs.config["train_end"] == "2020-01-04T00:00:00"
    assert inputs.config["user_limit"] == 2
    assert inputs.config["track_limit"] is None


def test_full_history_reuses_cache_and_includes_cold_users(music4all_factory, tmp_path):
    adapter, inputs = prepared_adapter(
        history_source(music4all_factory), tmp_path / "store", selection="full", track_limit=1
    )
    catalog = adapter.load(inputs)
    assert [user.source_id for user in catalog.users] == ["0000", "0001", "0002", "0003", "0004"]
    assert [track.source_id for track in catalog.tracks] == ["01", "02", "03", "04", "05", "99"]
    assert inputs.config["history_events"]["sha256"] == inputs.config["event_cache"]["sha256"]
    assert inputs.config["history_events"]["row_count"] == 8
    assert len(inputs.artifacts) == 2


def test_history_seed_and_amount_are_reproducible_under_event_reordering(
    music4all_factory, tmp_path
):
    selections = []
    for name, rows, seed, amount in [
        ("first", HISTORY_ROWS, 1, 2),
        ("reversed", list(reversed(HISTORY_ROWS)), 1, 2),
        ("other_seed", HISTORY_ROWS, 2, 2),
        ("less", HISTORY_ROWS, 1, 1),
    ]:
        files = history_source(music4all_factory, name, rows)
        options = dict(
            selection="history", train_end="2020-01-04", selection_seed=seed, user_limit=amount
        )
        adapter, inputs = prepared_adapter(files, tmp_path / "store", **options)
        catalog = adapter.load(inputs)
        selections.append(([user.source_id for user in catalog.users], inputs))
        if name == "first":
            _, repeated = prepared_adapter(files, tmp_path / "store", **options)
            assert inputs.config == repeated.config
            assert inputs.files == repeated.files
    assert selections[0][0] == selections[1][0]
    assert selections[0][1].config["history_events"] == selections[1][1].config["history_events"]
    assert selections[0][0] != selections[2][0]
    assert len(selections[3][0]) == 1
    assert set(selections[3][0]) <= set(selections[0][0])
    assert all(set(users) <= {"0001", "0003", "0004"} for users, _ in selections)
    assert selections[0][1].config["seed"] == 1


@pytest.mark.parametrize(
    "options",
    [
        {"selection": "unknown"},
        {"selection": "history"},
        {"selection": "history", "train_end": "invalid"},
        {"selection": "history", "train_end": "2020-01-04T00:00:00Z"},
        {"selection": "history", "train_end": "2020-01-04", "selection_seed": True},
        {"selection": "metadata", "selection_seed": 1},
    ],
)
def test_adapter_rejects_invalid_history_selection(music4all_factory, options):
    from src.domain.datasets import ValidationError

    with pytest.raises(ValidationError):
        adapter_for(music4all_factory(), **options)


def test_history_requires_requested_number_of_train_users(music4all_factory, tmp_path):
    from src.domain.datasets import ValidationError

    with pytest.raises(ValidationError, match="fewer"):
        prepared_adapter(
            history_source(music4all_factory),
            tmp_path / "store",
            selection="history",
            train_end="2020-01-04",
            user_limit=4,
        )


def test_full_catalog_includes_tracks_without_any_events(music4all_factory, tmp_path):
    files = music4all_factory(tag_rows=[("01", "{}"), ("777", "{'ambient': 60}")])
    adapter, inputs = prepared_adapter(files, tmp_path / "store", selection="full")
    catalog = adapter.load(inputs)
    assert [track.source_id for track in catalog.tracks] == ["01", "02", "03", "777", "99"]
    assert next(track for track in catalog.tracks if track.source_id == "777").metadata == {
        "tags": {"ambient": 60}
    }
    assert catalog.stats["missing_metadata"] == 3
    assert inputs.config["history_events"]["row_count"] == 4


def test_history_catalog_excludes_tag_only_tracks(music4all_factory, tmp_path):
    files = music4all_factory(tag_rows=[("01", "{}"), ("777", "{'ambient': 60}")])
    adapter, inputs = prepared_adapter(
        files, tmp_path / "store", selection="history", train_end="2020-01-04"
    )
    assert [track.source_id for track in adapter.load(inputs).tracks] == ["01", "02"]
