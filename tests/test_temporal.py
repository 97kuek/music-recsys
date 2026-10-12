"""時刻境界・Warm Start・再聴除外を手計算の履歴で検証する。"""

import json
from datetime import datetime

import pytest


def events_file(tmp_path, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "events.parquet"
    pq.write_table(
        pa.table(
            {
                "user_id": [r[0] for r in rows],
                "item_id": [r[1] for r in rows],
                "event_time": [datetime.fromisoformat(r[2]) for r in rows],
            }
        ),
        path,
    )
    return path


def test_temporal_boundaries_refit_and_truth(tmp_path):
    from music_recsys.data import load_dataset
    from music_recsys.temporal import TemporalConfig, prepare_temporal

    path = events_file(
        tmp_path,
        [
            ("a", "x", "2017-03-28T23:59:59"),
            ("b", "y", "2017-03-28T23:59:59"),
            ("a", "y", "2017-03-29T00:00:00"),
            ("a", "y", "2017-03-29T00:00:01"),
            ("a", "z", "2018-09-20T23:59:59"),
            ("c", "x", "2018-09-20T23:59:59"),
            ("a", "y", "2018-09-21T00:00:00"),
            ("b", "z", "2018-09-21T00:00:00"),
            ("a", "w", "2018-09-21T00:00:01"),
            ("d", "x", "2018-09-21T00:00:01"),
        ],
    )
    result = prepare_temporal(
        path,
        tmp_path / "out",
        TemporalConfig(),
        provenance={"snapshot_id": "test-snapshot"},
        export_benchmarks=True,
    )
    assert [result.summary["partitions"][p]["events"] for p in ("train", "validation", "test")] == [
        2,
        4,
        4,
    ]
    val = load_dataset(result.files["validation_benchmark"], top_k=10)
    test = load_dataset(result.files["test_benchmark"], top_k=10)
    assert {r.user_id for r in val.requests} == {"a", "b"}
    assert {r.user_id for r in test.requests} == {"a", "b", "c"}
    byuser = {r.user_id: r for r in test.requests}
    assert set(byuser["a"].history) == {"x", "y", "z"}
    assert set(byuser["a"].candidate_ids) == set()
    assert len(test.train_interactions) == 6
    truths = {t.request_id: set(t.relevant_item_ids) for t in test.ground_truth}
    assert truths[byuser["b"].request_id] == {"z"}
    assert truths[byuser["a"].request_id] == set()
    assert set(val.catalog_ids) == {"x", "y"}
    for phase in ("validation", "test"):
        stats = result.summary["evaluation"][phase]
        assert stats["cold_user_events"] == 1
        assert stats["cold_item_events"] == 1
        assert stats["evaluable_users"] == 1
        assert stats["ground_truth_pairs"] == 1
    assert result.summary["leakage_checks"]["passed"] is True
    assert val.provenance["snapshot_id"] == "test-snapshot"
    assert test.provenance["phase"] == "test"
    # Same inputs/options produce byte-identical datasets (no run timestamp).
    again = prepare_temporal(
        path,
        tmp_path / "again",
        TemporalConfig(),
        provenance={"snapshot_id": "test-snapshot"},
        export_benchmarks=True,
    )
    assert result.files["test_benchmark"].read_bytes() == again.files["test_benchmark"].read_bytes()


def test_invalid_timestamp_and_boundary_rejected(tmp_path):
    from music_recsys.temporal import TemporalConfig, prepare_temporal

    with pytest.raises(ValueError):
        TemporalConfig(train_end="2020-01-01", validation_end="2019-01-01")
    with pytest.raises(ValueError):
        TemporalConfig(train_end="2017-03-29T00:00:00+00:00")
    path = events_file(tmp_path, [("a", "x", "2016-01-01")])
    result = prepare_temporal(
        path, tmp_path / "out", TemporalConfig(), provenance={}, export_benchmarks=True
    )
    assert result.summary["evaluation"]["test"]["evaluable_users"] == 0
    assert (
        json.loads(result.files["test_benchmark"].read_text())["ground_truth"][0][
            "relevant_item_ids"
        ]
        == []
    )


def test_future_changes_do_not_change_training_or_candidates(tmp_path):
    from music_recsys.data import load_dataset
    from music_recsys.evaluation.benchmark import run_benchmark
    from music_recsys.evaluation.tracking import TrackingConfig
    from music_recsys.recommenders import create_recommender
    from music_recsys.temporal import TemporalConfig, prepare_temporal

    rows = [("a", "x", "2016-01-01"), ("b", "y", "2016-01-01"), ("a", "y", "2018-01-01")]
    original = prepare_temporal(
        events_file(tmp_path, rows),
        tmp_path / "original",
        TemporalConfig(),
        provenance={"snapshot_id": "fixed"},
        export_benchmarks=True,
    )
    changed = prepare_temporal(
        events_file(tmp_path, rows + [("a", "new", "2020-01-01")] * 3),
        tmp_path / "changed",
        TemporalConfig(),
        provenance={"snapshot_id": "fixed"},
        export_benchmarks=True,
    )
    assert (
        original.files["validation_benchmark"].read_bytes()
        == changed.files["validation_benchmark"].read_bytes()
    )
    first = load_dataset(original.files["test_benchmark"], top_k=10)
    second = load_dataset(changed.files["test_benchmark"], top_k=10)
    assert first.train_interactions == second.train_interactions
    assert first.requests == second.requests
    model = create_recommender("popularity", first.train_interactions)
    reports = [
        run_benchmark(
            first, model.recommend, model_name="popularity", seed=s, tracking=TrackingConfig()
        )
        for s in (0, 42)
    ]
    assert reports[0].metadata.evaluation_conditions["snapshot_id"] == "fixed"
    assert [r.response for r in reports[0].runs] == [r.response for r in reports[1].runs]
    assert [r.evaluation for r in reports[0].runs] == [r.evaluation for r in reports[1].runs]


def test_export_saved_partition_uses_refit_history(tmp_path):
    from music_recsys.data import load_dataset
    from music_recsys.temporal import TemporalConfig, export_phase, prepare_temporal

    events = events_file(
        tmp_path,
        [
            ("a", "x", "2016-01-01"),
            ("b", "y", "2016-01-01"),
            ("a", "y", "2018-01-01"),
            ("b", "x", "2020-01-01"),
        ],
    )
    split = prepare_temporal(events, tmp_path / "split", TemporalConfig(), provenance={})
    target = tmp_path / "test.json"
    export_phase(
        split.files, target, TemporalConfig(), phase="test", provenance={"split_id": "fixed"}
    )
    data = load_dataset(target, top_k=10)
    assert data.provenance["split_id"] == "fixed"
    assert len(data.train_interactions) == 3
    assert len(data.ground_truth[1].relevant_item_ids) == 1
