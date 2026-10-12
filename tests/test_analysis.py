"""分析表が比較不能な条件や生のユーザーIDを混ぜないことを検証する。"""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from music_recsys import analysis
from music_recsys.data import load_dataset
from music_recsys.evaluation.benchmark import run_experiment, save_report
from music_recsys.evaluation.tracking import TrackingConfig
from music_recsys.ingestion.types import Catalog, TrackRecord, UserRecord

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def report():
    return run_experiment(ROOT / "configs/toy.toml", tracking=TrackingConfig(enabled=False))


def test_comparison_accepts_saved_full_and_mlflow_aggregate_reports(report, tmp_path):
    directory = save_report(report, tmp_path)
    aggregate = {"metadata": asdict(report.metadata), "summary": asdict(report.summary)}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(aggregate))
    rows = analysis.compare_reports({"local": directory / "results.json", "mlflow": path})
    assert [(row["label"], row["model"], row["recall"]) for row in rows] == [
        ("local", "popularity", 0.625),
        ("mlflow", "popularity", 0.625),
    ]
    assert rows[0]["n_evaluated"] == 4
    assert rows[0]["n_excluded"] == 1


@pytest.mark.parametrize(
    "field,value", [("dataset_id", "other"), ("dataset_sha256", "other"), ("top_k", 3)]
)
def test_comparison_rejects_different_dataset_split_or_top_k(report, field, value):
    altered = asdict(report)
    altered["metadata"][field] = value
    with pytest.raises(ValueError, match=field):
        analysis.compare_reports({"baseline": report, "different": altered})


def test_comparison_rejects_missing_split_provenance(report):
    altered = asdict(report)
    altered["metadata"]["dataset_sha256"] = None
    with pytest.raises(ValueError, match="dataset_sha256"):
        analysis.compare_reports({"unknown": altered})


def test_request_analysis_keeps_undefined_metrics_and_omits_user_ids(report):
    rows = analysis.request_rows(report)
    assert rows[0]["history_count"] == 2
    assert rows[0]["candidate_count"] == 4
    assert rows[0]["history_overlap_count"] == 0
    assert rows[-1]["ndcg"] is None
    assert rows[-1]["recall"] is None
    assert all("user_id" not in row and "request_id" not in row for row in rows)


def test_recommendation_analysis_uses_training_popularity_and_separate_truth(report):
    dataset = load_dataset(ROOT / "examples/toy/dataset.json", top_k=2)
    rows = analysis.recommendation_rows(dataset, report)
    assert rows[:2] == [
        {
            "request_index": 1,
            "rank": 1,
            "item_id": "track_c",
            "train_events": 2,
            "is_relevant": True,
        },
        {
            "request_index": 1,
            "rank": 2,
            "item_id": "track_d",
            "train_events": 1,
            "is_relevant": False,
        },
    ]
    assert len(rows) == 10
    assert all(row["is_relevant"] is None for row in rows if row["request_index"] == 5)


def test_recommendation_analysis_rejects_a_different_dataset(report):
    dataset = load_dataset(ROOT / "examples/toy/dataset.json", top_k=3)
    with pytest.raises(ValueError, match="top_k"):
        analysis.recommendation_rows(dataset, report)


def test_tag_analysis_counts_tracks_and_preserves_weight_without_users():
    catalog = Catalog(
        (
            TrackRecord("a", {"tags": {"rock": 80, "pop": 20}}),
            TrackRecord("b", {"tags": {"rock": 40}}),
            TrackRecord("c", None),
            TrackRecord("d", {"tags": {}}),
        ),
        (UserRecord("private-user"),),
        {},
    )
    assert analysis.tag_rows(catalog) == [
        {"tag": "rock", "n_tracks": 2, "mean_weight": 60.0},
        {"tag": "pop", "n_tracks": 1, "mean_weight": 20.0},
    ]


def test_popularity_analysis_includes_zero_count_catalog_tracks(report):
    dataset = load_dataset(ROOT / "examples/toy/dataset.json", top_k=2)
    rows = analysis.popularity_rows(dataset, report)
    # track_aは履歴にない要求3と、履歴なしの要求4・5に現れる。
    assert rows[0] == {"item_id": "track_a", "train_events": 4, "recommended_count": 3}
    assert rows[-1] == {"item_id": "track_f", "train_events": 0, "recommended_count": 0}
    assert sum(row["recommended_count"] for row in rows) == 10


def test_dataset_request_analysis_does_not_expose_user_or_history():
    dataset = load_dataset(ROOT / "examples/toy/dataset.json", top_k=2)
    rows = analysis.dataset_request_rows(dataset)
    assert rows[0] == {"request_index": 1, "history_count": 2, "candidate_count": 4, "top_k": 2}
    assert rows[-1] == {"request_index": 5, "history_count": 0, "candidate_count": 6, "top_k": 2}


def test_temporal_analysis_cumulative_fraction_counts_all_days():
    profile = {
        "daily_counts": [
            {"day": "1970-01-01", "n_events": 1},
            {"day": "2020-01-01", "n_events": 2},
            {"day": "2020-01-02", "n_events": 7},
        ]
    }
    rows = analysis.temporal_rows(profile)
    assert [row["cumulative_fraction"] for row in rows] == [0.1, 0.3, 1.0]
    assert [row["day"].isoformat() for row in rows] == [
        "1970-01-01T00:00:00",
        "2020-01-01T00:00:00",
        "2020-01-02T00:00:00",
    ]


def test_saved_split_candidate_analysis_preserves_exclusive_pair_rates():
    profile = {
        "split_events": {"train": 8, "validation": 1, "test": 1},
        "split_fractions": {"train": 0.8, "validation": 0.1, "test": 0.1},
        **{
            phase: {
                "pair_reasons": {"cold_user": 1, "cold_item": 2, "seen": 3, "eligible": 4},
                "novel_users": 2,
                "novel_pairs": 4,
            }
            for phase in ("validation", "test_train_only", "test_refit_train_validation")
        },
    }
    tables = analysis.split_candidate_tables(profile)
    assert tables["events"][0] == {"split": "train", "n_events": 8, "event_fraction": 0.8}
    assert tables["eligibility"][0] == {
        "condition": "validation",
        "unique_pairs": 10,
        "cold_user_pairs": 1,
        "cold_item_pairs": 2,
        "cold_user_fraction": 0.1,
        "cold_item_fraction": 0.2,
        "novel_truth_pairs": 4,
        "users_with_novel_truth": 2,
    }
    profile["validation"].update({"pair_reasons": {}, "novel_pairs": 0, "novel_users": 0})
    empty = analysis.split_candidate_tables(profile)["eligibility"][0]
    assert empty["cold_user_fraction"] is None
    assert empty["cold_item_fraction"] is None
