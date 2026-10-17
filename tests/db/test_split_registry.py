"""分割台帳の原子的公開、不変性、成果物検証を実DBで確認する。"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy.exc import IntegrityError
from test_publication import members, new_run, snapshot
from test_schema import artifact, dataset, execute, source_catalog
from test_snapshot_frames import saved_snapshot

from src.ingestion.artifacts import ArtifactStore
from src.ingestion.types import ValidationError

pytestmark = pytest.mark.db
CONFIG = {
    "preprocessing_version": "test-v1",
    "dependency_lock_sha256": "a" * 64,
    "protocol": "temporal-warm-unseen-v1",
    "train_end": "2017-03-29T00:00:00",
    "validation_end": "2018-09-21T00:00:00",
    "timestamp_interpretation": "source_naive_timezone_unverified",
}
EVENTS = (
    b"user_id\ttrack_id\ttimestamp\n"
    b"a\t01\t2016-01-01 00:00:00\n"
    b"b\t02\t2016-02-01 00:00:00\n"
    b"a\t02\t2018-01-01 00:00:00\n"
    b"b\t01\t2020-01-01 00:00:00\n"
)


def stats(events, users, tracks, minimum, maximum):
    return dict(events=events, users=users, tracks=tracks, min_time=minimum, max_time=maximum)


SUMMARY = {
    "temporal": {k: CONFIG[k] for k in ("train_end", "validation_end", "timestamp_interpretation")},
    "source": stats(4, 2, 2, "2016-01-01T00:00:00", "2020-01-01T00:00:00"),
    "partitions": {
        "train": stats(2, 2, 2, "2016-01-01T00:00:00", "2016-02-01T00:00:00"),
        "validation": stats(1, 1, 1, "2018-01-01T00:00:00", "2018-01-01T00:00:00"),
        "test": stats(1, 1, 1, "2020-01-01T00:00:00", "2020-01-01T00:00:00"),
    },
}


def history_snapshot(db, music4all_factory, tmp_path, *, name="history_source", data=EVENTS):
    from src.adapters.music4all import Music4AllAdapter
    from src.ingestion.pipeline import import_dataset

    files = music4all_factory(name=name, events_data=data)
    adapter = Music4AllAdapter(
        record_path=files["record"],
        tags_path=files["tags"],
        events_path=files["events"],
        event_cache=files["cache"],
        version_label="v2",
        track_limit=3,
        user_limit=2,
        selection="full",
    )
    root = tmp_path / "artifacts"
    result = import_dataset(adapter, db, root, Path("uv.lock"))
    events = ArtifactStore(root, "music4all-onion").put(files["cache"], format="parquet")
    return root, result.snapshot_id, events


@pytest.fixture
def split_inputs(db, music4all_factory, tmp_path):
    root, snapshot_id, events = history_snapshot(db, music4all_factory, tmp_path)
    store = ArtifactStore(root, "music4all-onion")
    artifacts = {"events": events}
    source = pq.read_table(events.path)
    for role, start, length in (("train", 0, 2), ("validation", 2, 1), ("test", 3, 1)):
        path = tmp_path / f"{role}.parquet"
        pq.write_table(source.slice(start, length), path)
        artifacts[role] = store.put(path, format="parquet")
    return root, snapshot_id, artifacts


def publish(db, inputs, **overrides):
    from src.ingestion.splits import publish_split

    root, snapshot_id, artifacts = inputs
    params = dict(
        storage_root=root,
        snapshot_id=snapshot_id,
        config=CONFIG,
        artifacts=artifacts,
        summary=SUMMARY,
    )
    return publish_split(db, **(params | overrides))


def test_split_roundtrip_and_identical_rerun_reuse(db, split_inputs):
    from src.ingestion.splits import load_split

    split_id = publish(db, split_inputs)
    assert publish(db, split_inputs) == split_id
    loaded = load_split(db, split_inputs[0], split_id)
    assert loaded["id"] == split_id
    assert loaded["snapshot_id"] == split_inputs[1]
    assert loaded["dataset_key"] == "music4all-onion"
    assert loaded["version_label"] == "v2"
    assert loaded["dataset_version_id"]
    assert loaded["config"] == CONFIG
    assert loaded["summary"] == SUMMARY
    assert {k: a.sha256 for k, a in loaded["artifacts"].items()} == {
        k: a.sha256 for k, a in split_inputs[2].items()
    }
    assert publish(db, split_inputs, config=CONFIG | {"seed": 3}) != split_id
    assert publish(db, split_inputs, summary=SUMMARY | {"extra": 1}) != split_id


def test_concurrent_identical_publication_reuses_one_split(db, split_inputs):
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: publish(db, split_inputs), range(2)))
    assert ids[0] == ids[1]
    with db.connect() as c:
        assert execute(c, "SELECT count(*) FROM benchmark_splits").scalar_one() == 1


@pytest.mark.parametrize("role", ["validation_benchmark", "test_benchmark"])
def test_optional_benchmark_artifacts_roundtrip(db, split_inputs, tmp_path, role):
    from src.ingestion.splits import load_split

    path = tmp_path / "benchmark.json"
    path.write_text('{"users": []}')
    a = ArtifactStore(split_inputs[0], "music4all-onion").put(path, format="json")
    split_id = publish(db, split_inputs, artifacts=split_inputs[2] | {role: a})
    assert load_split(db, split_inputs[0], split_id)["artifacts"][role].sha256 == a.sha256


@pytest.mark.parametrize("change", ["missing", "extra", "format", "row_count", "dataset"])
def test_invalid_artifacts_leave_no_split_or_new_registry_rows(db, split_inputs, change):
    artifacts = dict(split_inputs[2])
    if change == "missing":
        del artifacts["test"]
    elif change == "extra":
        artifacts["unknown"] = artifacts["test"]
    elif change == "format":
        artifacts["test"] = replace(artifacts["test"], format="json")
    elif change == "row_count":
        artifacts["test"] = replace(artifacts["test"], row_count=999)
    else:
        artifacts["test"] = replace(artifacts["test"], storage_key="dataset-other/fake.parquet")
    with db.connect() as c:
        count = execute(c, "SELECT count(*) FROM data_artifacts").scalar_one()
    with pytest.raises(ValidationError):
        publish(db, split_inputs, artifacts=artifacts)
    with db.connect() as c:
        assert execute(c, "SELECT count(*) FROM benchmark_splits").scalar_one() == 0
        assert execute(c, "SELECT count(*) FROM data_artifacts").scalar_one() == count


@pytest.mark.parametrize("field", ["config", "summary"])
def test_non_object_metadata_is_rejected(db, split_inputs, field):
    with pytest.raises(ValidationError):
        publish(db, split_inputs, **{field: []})


def test_unknown_snapshot_and_split_rejected(db, split_inputs):
    from src.ingestion.splits import load_split

    with pytest.raises(ValidationError, match="published snapshot"):
        publish(db, split_inputs, snapshot_id=uuid4())
    with pytest.raises(ValidationError, match="split"):
        load_split(db, split_inputs[0], uuid4())


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE benchmark_splits SET config = '{}'",
        "UPDATE benchmark_splits SET train_artifact_id = test_artifact_id",
        "UPDATE benchmark_splits SET validation_benchmark_artifact_id = test_artifact_id",
        "DELETE FROM benchmark_splits",
        "TRUNCATE benchmark_splits",
    ],
)
def test_split_and_all_role_memberships_are_immutable(db, split_inputs, statement):
    publish(db, split_inputs)
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            execute(c, statement)


def test_loader_and_publisher_reject_tampered_file(db, split_inputs):
    from src.ingestion.splits import load_split

    split_id = publish(db, split_inputs)
    split_inputs[2]["train"].path.write_bytes(b"tampered")
    with pytest.raises(ValidationError, match="checksum"):
        load_split(db, split_inputs[0], split_id)
    with pytest.raises(ValidationError, match="checksum"):
        publish(db, split_inputs)


def clone_split(c, split_id, *, recalculate=False, **overrides):
    row = dict(
        execute(c, "SELECT * FROM benchmark_splits WHERE id = :s", s=split_id).mappings().one()
    )
    row.update(id=uuid4(), **overrides)
    if recalculate:
        from src.ingestion.types import fingerprint

        row["content_fingerprint"] = fingerprint(
            {
                "snapshot_id": str(row["snapshot_id"]),
                "config": row["config"],
                "artifacts": row["artifact_manifest"],
                "summary": row["summary"],
            }
        )
    # DBに渡すJSONは、通常のSQLクライアントと同じく明示的に符号化する。
    from src.ingestion.types import canonical_json

    for key in ("config", "summary", "artifact_manifest"):
        row[key] = canonical_json(row[key])
    columns = ", ".join(row)
    values = ", ".join(
        f"CAST(:{key} AS jsonb)" if key in ("config", "summary", "artifact_manifest") else f":{key}"
        for key in row
    )
    execute(c, f"INSERT INTO benchmark_splits ({columns}) VALUES ({values})", **row)


@pytest.mark.parametrize("field", ["config", "summary", "artifact_manifest", "content_fingerprint"])
def test_sql_insert_rejects_wrong_hash_or_manifest(db, split_inputs, field):
    split_id = publish(db, split_inputs)
    wrong = "0" * 64 if field == "content_fingerprint" else {"wrong": True}
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            clone_split(c, split_id, **{field: wrong})


@pytest.mark.parametrize("reference", ["artifact", "snapshot", "version"])
def test_sql_insert_rejects_cross_dataset_reference(db, split_inputs, reference):
    split_id = publish(db, split_inputs)
    with db.begin() as c:
        if reference == "artifact":
            other = artifact(c, dataset(c, "other"), format="parquet", rows=1)
            wrong = {"train_artifact_id": other}
        else:
            cat = source_catalog(c, "other")
            if reference == "version":
                wrong = {"dataset_version_id": cat["version"]}
            else:
                other = snapshot(c, cat, new_run(c, cat), members(c, cat))
                wrong = {"snapshot_id": other}
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            clone_split(c, split_id, recalculate=True, **wrong)


def test_sql_insert_rejects_unpublished_snapshot(db, split_inputs):
    split_id = publish(db, split_inputs)
    with pytest.raises(IntegrityError, match="published snapshot"):
        with db.begin() as c:
            cat = source_catalog(c, "other")
            unpublished = snapshot(c, cat, new_run(c, cat), members(c, cat), publish=False)
            clone_split(c, split_id, recalculate=True, snapshot_id=unpublished)


def test_failed_registration_rolls_back_all_new_references(db, split_inputs):
    a = split_inputs[2]["test"]
    with db.begin() as c:
        d = execute(
            c, "SELECT dataset_id FROM metadata_snapshots WHERE id = :s", s=split_inputs[1]
        ).scalar_one()
        # 最後のroleの既存台帳がファイルと食い違う。先行roleのINSERTもrollbackされる。
        artifact(c, d, checksum=a.sha256, format="parquet", rows=999)
        before = execute(c, "SELECT count(*) FROM data_artifacts").scalar_one()
    with pytest.raises(ValidationError, match="immutable registry"):
        publish(db, split_inputs)
    with db.connect() as c:
        assert execute(c, "SELECT count(*) FROM benchmark_splits").scalar_one() == 0
        assert execute(c, "SELECT count(*) FROM data_artifacts").scalar_one() == before


def test_split_is_visible_only_after_complete_row_commits(db, split_inputs):
    split_id = publish(db, split_inputs)
    with db.connect() as writer, db.connect() as reader:
        with writer.begin():
            clone_split(writer, split_id, recalculate=True, config=CONFIG | {"seed": 9})
            assert execute(reader, "SELECT count(*) FROM benchmark_splits").scalar_one() == 1
        assert execute(reader, "SELECT count(*) FROM benchmark_splits").scalar_one() == 2


def test_malformed_optional_json_rejected(db, split_inputs, tmp_path):
    path = tmp_path / "malformed.json"
    path.write_text("{broken")
    a = ArtifactStore(split_inputs[0], "music4all-onion").put(path, format="json")
    with pytest.raises(ValidationError, match="JSON"):
        publish(db, split_inputs, artifacts=split_inputs[2] | {"validation_benchmark": a})


def changed_partition(inputs, tmp_path, role, table):
    path = tmp_path / "changed.parquet"
    pq.write_table(table, path)
    a = ArtifactStore(inputs[0], "music4all-onion").put(path, format="parquet")
    return inputs[2] | {role: a}


def assert_rejected_without_publication(db, inputs, **overrides):
    with db.connect() as c:
        before = execute(c, "SELECT count(*) FROM data_artifacts").scalar_one()
    with pytest.raises(ValidationError):
        publish(db, inputs, **overrides)
    with db.connect() as c:
        assert execute(c, "SELECT count(*) FROM benchmark_splits").scalar_one() == 0
        assert execute(c, "SELECT count(*) FROM data_artifacts").scalar_one() == before


def test_split_rejects_snapshot_without_history(db, split_inputs, music4all_factory, tmp_path):
    _, snapshot_id = saved_snapshot(db, music4all_factory, tmp_path)
    assert_rejected_without_publication(db, split_inputs, snapshot_id=snapshot_id)


@pytest.mark.parametrize("replace_events", [False, True])
def test_split_rejects_other_snapshot_history(
    db, split_inputs, music4all_factory, tmp_path, replace_events
):
    _, other_snapshot, other_events = history_snapshot(
        db,
        music4all_factory,
        tmp_path,
        name="other_history",
        data=EVENTS.replace(b"2016-01-01", b"2015-01-01"),
    )
    if replace_events:
        assert_rejected_without_publication(
            db, split_inputs, artifacts=split_inputs[2] | {"events": other_events}
        )
    else:
        assert_rejected_without_publication(db, split_inputs, snapshot_id=other_snapshot)


def test_loader_rejects_registry_referencing_other_snapshot_history(
    db, split_inputs, music4all_factory, tmp_path
):
    from src.ingestion.splits import load_split

    split_id = publish(db, split_inputs)
    _, other_snapshot, _ = history_snapshot(
        db,
        music4all_factory,
        tmp_path,
        name="other_history",
        data=EVENTS.replace(b"2016-01-01", b"2015-01-01"),
    )
    with db.begin() as c:
        version = execute(
            c, "SELECT dataset_version_id FROM metadata_snapshots WHERE id=:s", s=other_snapshot
        ).scalar_one()
        clone_split(
            c, split_id, recalculate=True, snapshot_id=other_snapshot, dataset_version_id=version
        )
        wrong_id = execute(
            c, "SELECT id FROM benchmark_splits WHERE id<>:s", s=split_id
        ).scalar_one()
    with pytest.raises(ValidationError, match="history"):
        load_split(db, split_inputs[0], wrong_id)


def test_train_and_test_role_swap_rejected(db, split_inputs):
    artifacts = split_inputs[2] | {
        "train": split_inputs[2]["test"],
        "test": split_inputs[2]["train"],
    }
    assert_rejected_without_publication(db, split_inputs, artifacts=artifacts, summary={})


@pytest.mark.parametrize("boundary", ["train_end", "validation_end"])
def test_partitions_must_obey_config_boundaries(db, split_inputs, boundary):
    assert_rejected_without_publication(
        db,
        split_inputs,
        config=CONFIG
        | {boundary: "2016-01-15T00:00:00" if boundary == "train_end" else "2017-10-01T00:00:00"},
        summary={},
    )


@pytest.mark.parametrize("change", ["missing", "duplicate", "foreign_event"])
def test_partition_multiset_must_equal_source(db, split_inputs, tmp_path, change):
    train = pq.read_table(split_inputs[2]["train"].path)
    if change == "missing":
        train = train.slice(0, 1)
    elif change == "duplicate":
        train = pa.concat_tables([train.slice(0, 1), train.slice(0, 1)])
    else:
        train = train.set_column(0, "user_id", pa.array(["x", "b"]))
    artifacts = changed_partition(split_inputs, tmp_path, "train", train)
    # generic config/summaryでもmultiset整合は必須。
    assert_rejected_without_publication(
        db, split_inputs, artifacts=artifacts, config={}, summary={}
    )


@pytest.mark.parametrize("change", ["numeric_id", "timezone", "missing_column", "null", "blank"])
def test_partition_schema_and_ids_validated(db, split_inputs, tmp_path, change):
    train = pq.read_table(split_inputs[2]["train"].path)
    if change == "numeric_id":
        train = train.set_column(0, "user_id", pa.array([1, 2]))
    elif change == "timezone":
        train = train.set_column(
            2, "event_time", train["event_time"].cast(pa.timestamp("us", tz="UTC"))
        )
    elif change == "missing_column":
        train = train.drop(["event_time"])
    else:
        train = train.set_column(0, "user_id", pa.array([None if change == "null" else "\t", "b"]))
    artifacts = changed_partition(split_inputs, tmp_path, "train", train)
    assert_rejected_without_publication(db, split_inputs, artifacts=artifacts)


@pytest.mark.parametrize(
    "field,value",
    [
        ("events", 99),
        ("users", 1),
        ("tracks", 1),
        ("min_time", "2015-01-01T00:00:00"),
        ("max_time", None),
    ],
)
def test_temporal_summary_must_match_actual_partitions(db, split_inputs, field, value):
    summary = deepcopy(SUMMARY)
    summary["partitions"]["train"][field] = value
    assert_rejected_without_publication(db, split_inputs, summary=summary)


def test_temporal_summary_source_must_match_actual_events(db, split_inputs):
    summary = deepcopy(SUMMARY)
    summary["source"]["users"] = 1
    assert_rejected_without_publication(db, split_inputs, summary=summary)


@pytest.mark.parametrize("case", ["duplicates", "empty_partitions", "exact_boundaries"])
def test_valid_history_multiplicity_and_boundary_cases(db, music4all_factory, tmp_path, case):
    if case == "duplicates":
        data = EVENTS.replace(b"a\t01\t2016-01-01 00:00:00\n", b"a\t01\t2016-01-01 00:00:00\n" * 2)
    elif case == "empty_partitions":
        data = b"\n".join(EVENTS.split(b"\n")[:3]) + b"\n"
    else:
        data = EVENTS.replace(b"2018-01-01", b"2017-03-29").replace(b"2020-01-01", b"2018-09-21")
    root, snapshot_id, events = history_snapshot(db, music4all_factory, tmp_path, data=data)
    from src.temporal import TemporalConfig, prepare_temporal

    result = prepare_temporal(events.path, tmp_path / "split", TemporalConfig(), provenance={})
    store = ArtifactStore(root, "music4all-onion")
    artifacts = {role: store.put(path, format="parquet") for role, path in result.files.items()}
    split_id = publish(db, (root, snapshot_id, artifacts), summary=result.summary)
    from src.ingestion.splits import load_split

    loaded = load_split(db, root, split_id)
    assert loaded["artifacts"]["events"].row_count == (
        5 if case == "duplicates" else 2 if case == "empty_partitions" else 4
    )
    assert loaded["summary"] == result.summary


def test_loader_rejects_self_consistent_hash_with_invalid_summary_counts(db, split_inputs):
    from src.ingestion.splits import load_split

    split_id = publish(db, split_inputs)
    summary = deepcopy(SUMMARY)
    summary["partitions"]["train"]["events"] = 999
    with db.begin() as c:
        clone_split(c, split_id, recalculate=True, summary=summary)
        wrong_id = execute(
            c, "SELECT id FROM benchmark_splits WHERE id<>:s", s=split_id
        ).scalar_one()
    with pytest.raises(ValidationError, match="counts"):
        load_split(db, split_inputs[0], wrong_id)
