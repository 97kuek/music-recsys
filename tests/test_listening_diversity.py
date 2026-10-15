import math
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from music_recsys.data import Interaction
from music_recsys.listening_types import ListeningDataset
from music_recsys.types import RecommendationRequest, RecommendationResponse


def fixture(items=("invalid", "a", "a", "b"), *, repeat=False, failed=False):
    request = RecommendationRequest(
        "r", "u", ("s",), ("s",) if repeat else ("a", "b"), 20, task="repeat" if repeat else "new"
    )
    dataset = ListeningDataset(
        "toy",
        ("a", "b", "s"),
        (Interaction("u", "s"), Interaction("v", "a"), Interaction("w", "b")),
        (request,),
        Path("/unreadable-future"),
        {},
        "fixture",
    )
    response = RecommendationResponse(
        "r",
        () if failed else tuple(items),
        status="error" if failed else "ok",
        error_type="Failure" if failed else None,
    )
    return (
        dataset,
        [{"request": asdict(request), "response": asdict(response)}],
        {"a": {"rock": 1}, "b": {"jazz": 1}},
    )


def test_top_k_holes_are_preserved_and_popularity_uses_only_learning_users():
    from music_recsys.evaluation.listening_diversity import listening_diversity

    data, runs, features = fixture()
    result = listening_diversity(data, runs, ks=(1, 2, 3, 4), features=features)
    rows = {r["k"]: r for r in result["metrics"]}
    assert rows[1]["novelty"] is None
    assert rows[2]["novelty"] == pytest.approx(-math.log2(2 / 5))
    assert rows[2]["catalog_coverage"] == 0.5
    assert rows[3]["catalog_coverage"] == 0.5
    assert rows[4]["catalog_coverage"] == 1
    assert all(rows[k]["ild"] is None for k in (1, 2, 3))
    assert rows[4]["ild"] == 1
    assert rows[1]["novelty_excluded"] == 1
    assert rows[4]["ild_evaluated"] == 1


@pytest.mark.parametrize(
    "features",
    [None, {"a": {"rock": 1}}, {"a": {"rock": 1}, "b": {}}, {"a": {"rock": 1}, "b": {"rock": 0}}],
)
def test_missing_or_zero_features_do_not_become_zero_distance(features):
    from music_recsys.evaluation.listening_diversity import listening_diversity

    data, runs, _ = fixture(("a", "b"))
    row = listening_diversity(data, runs, ks=(2,), features=features)["metrics"][0]
    assert row["ild"] is None
    assert row["ild_excluded"] == 1
    assert row["novelty"] is not None


def test_repeat_candidates_and_empty_or_failed_outputs_have_explicit_denominators():
    from music_recsys.evaluation.listening_diversity import listening_diversity

    data, runs, _ = fixture(("s", "a"), repeat=True)
    row = listening_diversity(data, runs, ks=(2,))["metrics"][0]
    assert row["task"] == "repeat" and row["catalog_coverage"] == 1
    assert row["ild"] is None
    data, runs, _ = fixture(failed=True)
    row = listening_diversity(data, runs, ks=(2,))["metrics"][0]
    assert row["catalog_coverage"] == 0 and row["novelty"] is None
    assert row["n_failed"] == 1
    request = replace(data.requests[0], candidate_ids=())
    data = replace(data, requests=(request,))
    runs[0]["request"] = asdict(request)
    assert listening_diversity(data, runs, ks=(2,))["metrics"][0]["catalog_coverage"] is None


def test_runner_stores_diversity_once_for_many_windows_and_gains(tmp_path):
    import json

    from listening_support import write_bundle

    from music_recsys.evaluation.listening_benchmark import (
        run_listening_benchmark,
        save_listening_report,
    )
    from music_recsys.listening_data import load_listening_dataset
    from music_recsys.listening_types import ListeningConfig

    config = ListeningConfig(window_days=(90,), day_weights=(0.0, 1.0, 2.0))
    data = load_listening_dataset(write_bundle(tmp_path / "bundle"), config)
    calls = []

    def recommend(request):
        calls.append(request.request_id)
        return RecommendationResponse(request.request_id, request.candidate_ids)

    report = run_listening_benchmark(
        data,
        recommend,
        config=config,
        model_name="fixture",
        features={"a": {"rock": 1}, "b": {"jazz": 1}},
    )
    assert len(calls) == 4
    assert len(report.beyond_accuracy["metrics"]) == 38
    path = save_listening_report(report, tmp_path / "results")
    saved = json.loads((path / "results.json").read_text())
    assert saved["beyond_accuracy"]["definitions"]["ranking"]
    assert (path / "beyond-accuracy.csv").exists()


def test_large_finite_identical_vectors_have_zero_distance():
    from music_recsys.evaluation.listening_diversity import listening_diversity

    data, runs, _ = fixture(("a", "b"))
    features = {
        "a": {"x": 1e308, "y": 1e308, "z": 1e308, "w": 1e308},
        "b": {"x": 1e308, "y": 1e308, "z": 1e308, "w": 1e308},
    }
    assert listening_diversity(data, runs, ks=(2,), features=features)["metrics"][0][
        "ild"
    ] == pytest.approx(0.0, abs=1e-15)
