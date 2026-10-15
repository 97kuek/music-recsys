import json
import runpy
from pathlib import Path

import numpy as np
import pytest


@pytest.mark.parametrize("basis", ["play_count", "user_count"])
def test_streaming_matches_reference_all_conditions_and_populations(tmp_path, basis):
    from music_recsys.evaluation.listening_benchmark import run_listening_benchmark
    from music_recsys.evaluation.tracking import TrackingConfig
    from music_recsys.experiment_search import load_study_config
    from music_recsys.features import load_features
    from music_recsys.full_popularity import run
    from music_recsys.listening_data import load_listening_dataset
    from music_recsys.recommenders.popularity import PopularityRecommender

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = json.loads(Path("configs/popularity-plays.json").read_text())
    raw["model"]["parameters"] = {"basis": basis}
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(bundle / "test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "output"),
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw))
    config = load_study_config(path)
    data = load_listening_dataset(config.validation_dataset, config.evaluation)
    features = load_features(
        config.features,
        dataset_id=data.dataset_id,
        snapshot_id=data.manifest["source"]["snapshot_id"],
    )
    report = run_listening_benchmark(
        data,
        PopularityRecommender(data.train_interactions, basis=basis).recommend,
        config=config.evaluation,
        model_name="popularity",
        features=features,
        tracking=TrackingConfig(enabled=False),
    )
    output = run(path)
    result = json.loads((output / "result.json").read_text())

    def key(r):
        return (r["task"], r["window_days"], r["day_weight"], r["metric"], r["k"])

    actual = {key(r): r for r in result["accuracy"]}
    for row in report.summary:
        match = actual[key(row)]
        assert match["mean"] == pytest.approx(row["value"])
        assert match["n_evaluated"] == row["n_evaluated"]
        assert match["n_excluded"] == row["n_excluded"]
    for a, b in zip(result["diversity"], report.beyond_accuracy["metrics"], strict=True):
        for metric in (
            "ild",
            "novelty",
            "catalog_coverage",
            "ild_evaluated",
            "ild_excluded",
            "candidate_items",
            "exposed_items",
        ):
            assert a[metric] == pytest.approx(b[metric])
    import pyarrow.parquet as pq

    recommendations = pq.read_table(output / "recommendations.parquet").to_pylist()
    assert [(r["user_id"], r["task"], r["item_ids"]) for r in recommendations] == [
        (r["request"]["user_id"], r["request"]["task"], list(r["response"]["item_ids"]))
        for r in report.runs
    ]
    assert len(list(output.glob("*.png"))) == 16
    assert not list(output.glob("*.partial"))
    with pytest.raises(FileExistsError):
        run(path)


def test_vector_scores_match_reference_random_histories():
    from music_recsys.evaluation.listening_metrics import PreparedListeningResponse
    from music_recsys.listening_types import ListeningConfig, PlaybackObservation
    from music_recsys.streaming_metrics import accuracy_vectors
    from music_recsys.types import RecommendationRequest, RecommendationResponse

    config = ListeningConfig(day_weights=(0.0, 0.5, 1.0, 2.0))
    rng = np.random.default_rng(18)
    catalog = tuple(str(i) for i in range(40))
    for trial in range(10):
        history = set(rng.choice(catalog, 15, replace=False))
        observations = [
            PlaybackObservation(
                "u", i, w, int(i in history), int(i in history), int(rng.integers(1, 20)), 1
            )
            for w in config.window_days
            for i in rng.choice(catalog, 12, replace=False)
        ]
        for task in ("new", "repeat"):
            eligible = tuple(i for i in catalog if (i in history) == (task == "repeat"))
            ranking = tuple(rng.permutation(eligible)[:20])
            request = RecommendationRequest("r", "u", tuple(history), eligible, 20, task)
            prepared = PreparedListeningResponse(
                request, RecommendationResponse("r", ranking), observations
            )
            truth = [
                (o.item_id, o.window_days, o.evaluation_play_count, o.evaluation_active_days)
                for o in observations
                if o.item_id in eligible
            ]
            for (_, window, weight, metric), values in accuracy_vectors(
                ranking, truth, task=task, config=config
            ):
                expected = [
                    getattr(
                        prepared.evaluate(window_days=window, k=k, day_weight=weight or 0.0),
                        "recall" if metric == "repeat_recall" else metric,
                    )
                    for k in config.ks
                ]
                np.testing.assert_allclose(values, expected, rtol=1e-14)


def test_unique_users_reverse_play_count_ranking():
    from music_recsys.data import Interaction
    from music_recsys.recommenders.popularity import PopularityRecommender
    from music_recsys.types import RecommendationRequest

    events = [Interaction("u1", "a")] * 10 + [Interaction("u1", "b"), Interaction("u2", "b")]
    request = RecommendationRequest("r", "new-user", (), ("a", "b"), 2)
    assert PopularityRecommender(events).recommend(request).item_ids == ("a", "b")
    assert PopularityRecommender(events, basis="user_count").recommend(request).item_ids == (
        "b",
        "a",
    )
