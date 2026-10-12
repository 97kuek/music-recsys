"""DBで保証する所属・出典・ID・不変性の境界を検証する。"""

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.db


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def execute(connection, statement, **params):
    return connection.execute(text(statement), params)


def dataset(connection, key="music4all-onion"):
    return execute(
        connection, "INSERT INTO datasets (key) VALUES (:key) RETURNING id", key=key
    ).scalar_one()


def track(connection, dataset_id, source_id="000Ab"):
    return execute(
        connection,
        "INSERT INTO tracks (dataset_id, source_id) VALUES (:d, :source) RETURNING id",
        d=dataset_id,
        source=source_id,
    ).scalar_one()


def artifact(connection, dataset_id, checksum=None, rows=None, format="bz2"):
    checksum = checksum or hashlib.sha256(uuid4().bytes).hexdigest()
    return execute(
        connection,
        """INSERT INTO data_artifacts
           (dataset_id, format, storage_key, sha256, byte_size, row_count, verified_at)
           VALUES (:d, :format, :key, :hash, 20, :rows, now()) RETURNING id""",
        d=dataset_id,
        format=format,
        key=f"{dataset_id}/{checksum}",
        hash=checksum,
        rows=rows,
    ).scalar_one()


def source_catalog(connection, key="music4all-onion"):
    d = dataset(connection, key)
    manifest = {
        "files": [
            {
                "name": "tags.tsv.bz2",
                "byte_size": 20,
                "checksum": {"algorithm": "md5", "value": "a" * 32},
            }
        ]
    }
    version = execute(
        connection,
        """INSERT INTO dataset_versions
           (dataset_id, version_label, source_ref, source_manifest, manifest_sha256)
           VALUES (:d, 'v2', 'https://example.test/record/2', CAST(:manifest AS jsonb), :hash)
           RETURNING id""",
        d=d,
        manifest=json.dumps(manifest),
        hash=digest(manifest),
    ).scalar_one()
    a = artifact(connection, d)
    source = execute(
        connection,
        """INSERT INTO source_files
           (dataset_id, dataset_version_id, logical_name, source_url,
            expected_checksum_algorithm, expected_checksum, artifact_id)
           VALUES (:d, :v, 'tags.tsv.bz2', 'https://example.test/tags', 'md5', :md5, :a)
           RETURNING id""",
        d=d,
        v=version,
        a=a,
        md5="a" * 32,
    ).scalar_one()
    return {"dataset": d, "version": version, "source": source}


def metadata(connection, dataset_id, track_id, payload=None):
    payload = {"tags": {"rock": 80}} if payload is None else payload
    return execute(
        connection,
        """INSERT INTO track_metadata (dataset_id, track_id, payload, content_sha256)
           VALUES (:d, :t, CAST(:payload AS jsonb), :hash) RETURNING id""",
        d=dataset_id,
        t=track_id,
        payload=json.dumps(payload),
        hash=digest(payload),
    ).scalar_one()


def test_source_ids_preserve_case_and_leading_zero(db):
    with db.begin() as c:
        d = dataset(c)
        track(c, d, "000Ab")
        track(c, d, "000ab")
        other = dataset(c, "talkplay")
        track(c, other, "000Ab")
        assert sorted(execute(c, "SELECT source_id FROM tracks").scalars().all()) == [
            "000Ab",
            "000Ab",
            "000ab",
        ]


def test_same_dataset_source_id_cannot_be_duplicated(db):
    with db.begin() as c:
        d = dataset(c)
        track(c, d)
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            track(c, d)


@pytest.mark.parametrize("source", ["", " ", "\t\n"])
def test_blank_source_id_is_rejected(db, source):
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            track(c, dataset(c), source)


def test_metadata_cannot_reference_another_dataset(db):
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            d = dataset(c)
            other = dataset(c, "talkplay")
            metadata(c, other, track(c, d))


def test_metadata_hash_must_match_payload(db):
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            d = dataset(c)
            t = track(c, d)
            execute(
                c,
                """INSERT INTO track_metadata
                (dataset_id, track_id, payload, content_sha256)
                VALUES (:d, :t, '{"tags": {}}', :hash)""",
                d=d,
                t=t,
                hash="0" * 64,
            )


@pytest.mark.parametrize("operation", ["UPDATE tracks SET source_id = 'new'", "DELETE FROM tracks"])
def test_identity_is_immutable(db, operation):
    with db.begin() as c:
        track(c, dataset(c))
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            execute(c, operation)


@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE track_metadata SET payload = '{}'",
        "DELETE FROM track_metadata",
        "UPDATE dataset_versions SET version_label = 'changed'",
        "DELETE FROM source_files",
        "UPDATE data_artifacts SET storage_key = 'replacement'",
    ],
)
def test_provenance_and_metadata_are_immutable(db, operation):
    with db.begin() as c:
        cat = source_catalog(c)
        metadata(c, cat["dataset"], track(c, cat["dataset"]))
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            execute(c, operation)


@pytest.mark.parametrize(
    "name,checksum", [("not-in-version", "a" * 32), ("tags.tsv.bz2", "b" * 32)]
)
def test_source_file_must_match_version_manifest(db, name, checksum):
    with db.begin() as c:
        cat = source_catalog(c)
        a = artifact(c, cat["dataset"])
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            execute(
                c,
                """INSERT INTO source_files
                (dataset_id, dataset_version_id, logical_name, source_url,
                 expected_checksum_algorithm, expected_checksum, artifact_id)
                VALUES (:d, :v, :name, 'https://example.test/other', 'md5', :hash, :a)""",
                d=cat["dataset"],
                v=cat["version"],
                name=name,
                hash=checksum,
                a=a,
            )


def test_parquet_requires_verified_row_count(db):
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            artifact(c, dataset(c), format="parquet")


@pytest.mark.parametrize("key", ["/absolute", "../outside", "a/../b", "a//b", "a/./b"])
def test_artifact_storage_key_is_relative_and_normalized(db, key):
    with pytest.raises(IntegrityError):
        with db.begin() as c:
            d = dataset(c)
            execute(
                c,
                """INSERT INTO data_artifacts
                (dataset_id, format, storage_key, sha256, byte_size, verified_at)
                VALUES (:d, 'bz2', :key, :hash, 20, now())""",
                d=d,
                key=key,
                hash="a" * 64,
            )


def test_json_canonicalization_is_stable_for_unicode_and_numbers(db):
    with db.begin() as c:
        actual = execute(
            c,
            """SELECT recsys_canonical_json(
            '{"z": [true, null, 1.00, 1e-3], "a": "音楽"}'::jsonb)""",
        ).scalar_one()
        assert actual == '{"a":"音楽","z":[true,null,1,0.001]}'


def test_integral_decimal_file_size_uses_the_same_manifest_rules(db):
    normalized = {"files": [{"name": "tags", "byte_size": 20}]}
    with db.begin() as c:
        d = dataset(c)
        v = execute(
            c,
            """INSERT INTO dataset_versions
            (dataset_id, version_label, source_ref, source_manifest, manifest_sha256)
            VALUES (:d, 'v2', 'https://example.test/decimal',
                '{"files": [{"name": "tags", "byte_size": 20.0}]}', :hash) RETURNING id""",
            d=d,
            hash=digest(normalized),
        ).scalar_one()
        a = artifact(c, d)
        execute(
            c,
            """INSERT INTO source_files
            (dataset_id, dataset_version_id, logical_name, source_url, artifact_id)
            VALUES (:d, :v, 'tags', 'https://example.test/tags', :a)""",
            d=d,
            v=v,
            a=a,
        )
        assert execute(c, "SELECT count(*) FROM source_files").scalar_one() == 1
