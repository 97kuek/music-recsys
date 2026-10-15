import json

import pyarrow.parquet as pq
import pytest
from listening_support import write_partitions


def test_export_counts_dates_and_excludes_window_end(tmp_path):
    from music_recsys.listening_data import load_listening_dataset
    from music_recsys.listening_export import export_listening_phase
    from music_recsys.listening_types import ListeningConfig
    from music_recsys.temporal import TemporalConfig

    files = write_partitions(tmp_path / "source")
    out = tmp_path / "bundle"
    manifest = export_listening_phase(
        files, out, TemporalConfig(), phase="validation", provenance={}
    )
    actual = {
        (r["user_id"], r["item_id"], r["window_days"]): (
            r["evaluation_play_count"],
            r["evaluation_active_days"],
        )
        for r in pq.read_table(out / "observations.parquet").to_pylist()
    }
    assert actual == {
        ("u", "b", 30): (3, 2),
        ("u", "b", 90): (3, 2),
        ("u", "b", 180): (4, 3),
        ("u", "a", 90): (1, 1),
        ("u", "a", 180): (1, 1),
    }
    raw = json.loads(manifest.read_text())
    assert raw["exclusions"]["90"]["cold_user_events"] == 1
    assert raw["exclusions"]["90"]["cold_item_events"] == 1
    dataset = load_listening_dataset(out, ListeningConfig())
    assert dataset.catalog_ids == ("a", "b")
    assert len(dataset.requests) == 4
    assert set(raw["source_files"]) == {"train", "validation"}


def test_test_learning_includes_entire_train_and_validation(tmp_path):
    from music_recsys.listening_export import export_listening_phase
    from music_recsys.temporal import TemporalConfig

    files = write_partitions(tmp_path / "source")
    out = tmp_path / "test-bundle"
    export_listening_phase(files, out, TemporalConfig(), phase="test", provenance={})
    learning = {
        (r["user_id"], r["item_id"]): (r["play_count"], r["active_days"])
        for r in pq.read_table(out / "learning.parquet").to_pylist()
    }
    assert learning["u", "b"] == (4, 3)
    assert learning["u", "a"] == (2, 2)
    assert learning["cold", "a"] == (1, 1)
    assert learning["u", "cold_item"] == (1, 1)


def test_export_is_deterministic_and_validation_never_reads_test(tmp_path):
    from music_recsys.listening_export import export_listening_phase
    from music_recsys.temporal import TemporalConfig

    files = write_partitions(tmp_path / "source")
    first = export_listening_phase(
        files, tmp_path / "first", TemporalConfig(), phase="validation", provenance={}
    )
    files["test"].write_bytes(b"intentionally invalid future file")
    second = export_listening_phase(
        files, tmp_path / "second", TemporalConfig(), phase="validation", provenance={}
    )
    assert first.read_bytes() == second.read_bytes()
    for role in ("learning", "observations"):
        assert (first.parent / f"{role}.parquet").read_bytes() == (
            second.parent / f"{role}.parquet"
        ).read_bytes()
    with pytest.raises(FileExistsError):
        export_listening_phase(
            files, first.parent, TemporalConfig(), phase="validation", provenance={}
        )


def test_bad_partition_has_no_completed_manifest(tmp_path):
    import pyarrow as pa

    from music_recsys.listening_export import export_listening_phase
    from music_recsys.temporal import TemporalConfig

    files = write_partitions(tmp_path / "source")
    rows = pq.read_table(files["train"]).to_pylist()
    rows[0]["event_time"] = rows[0]["event_time"].replace(year=2020)
    pq.write_table(pa.Table.from_pylist(rows), files["train"])
    out = tmp_path / "invalid"
    with pytest.raises(ValueError):
        export_listening_phase(files, out, TemporalConfig(), phase="validation", provenance={})
    assert not (out / "manifest.json").exists()


def test_export_spills_multiple_buckets_with_small_memory(tmp_path):
    from music_recsys.listening_export import export_listening_phase
    from music_recsys.temporal import TemporalConfig

    files = write_partitions(tmp_path / "source")
    out = tmp_path / "bounded"
    export_listening_phase(
        files, out, TemporalConfig(), phase="validation", provenance={}, memory_limit="128MB"
    )
    assert pq.read_table(out / "learning.parquet").num_rows == 2


def test_existing_temporal_datetime_formats_are_accepted(tmp_path):
    from music_recsys.listening_export import export_listening_phase
    from music_recsys.temporal import TemporalConfig

    config = TemporalConfig(train_end="2017-03-29 00:00:00", validation_end="2018-09-21 00:00:00")
    target = export_listening_phase(
        write_partitions(tmp_path / "source"),
        tmp_path / "bundle",
        config,
        phase="validation",
        provenance={},
    )
    assert json.loads(target.read_text())["prediction_at"] == "2017-03-29T00:00:00"
