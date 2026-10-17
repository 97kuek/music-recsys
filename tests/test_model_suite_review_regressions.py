import json
import math
from dataclasses import asdict

import pytest
from model_suite_support import model_config
from test_listening_diversity import fixture

from src.domain.evaluation.dataset import Interaction
from src.domain.recommendation.contracts import RecommendationRequest
from src.infrastructure.recommenders import create_recommender


def test_bpr_items_never_updated_are_explicit_fallbacks():
    events = [Interaction("all", item) for item in "abcd"] + [Interaction("v", "a")]
    model = create_recommender("bpr", events, parameters={"factors": 2, "iterations": 1}, seed=0)
    request = RecommendationRequest("r", "v", ("a",), ("b", "c", "d"), 20)
    for item in ("b", "d"):
        detail = model.explain(request, item)
        assert detail["score"] is None and detail["fallback"] == "popularity"
    assert math.isfinite(model.explain(request, "c")["score"])
    assert model.recommend(request).item_ids == ("c", "b", "d")


@pytest.mark.parametrize("tracked", [False, True])
def test_standalone_binary_feature_changes_cannot_be_compared_as_same_conditions(study, tracked):
    from src.infrastructure.datasets.features import write_features
    from src.infrastructure.evaluation.benchmark import run_experiment
    from src.infrastructure.evaluation.tracking import TrackingConfig
    from src.interfaces.notebook.experiment_analysis import load_reports

    config = model_config(study(), "content")
    benchmark = config.path.parent / "benchmark.toml"
    benchmark.write_text(
        f'[benchmark]\ndataset="{config.validation_dataset}"\nfeatures="{config.features}"\n'
        'model="content"\ntop_k=20\nseed=0\n'
    )
    tracking = TrackingConfig(
        enabled=tracked, tracking_uri=f"sqlite:///{config.path.parent}/tracking.db"
    )
    first = run_experiment(benchmark, tracking=tracking)
    raw = json.loads(config.features.read_text())
    changed = config.path.parent / "changed.json"
    write_features(
        changed,
        dataset_id=raw["dataset_id"],
        snapshot_id=raw["snapshot_id"],
        source={},
        features={item: {"changed": 1} for item in raw["features"]},
    )
    config.features.write_bytes(changed.read_bytes())
    second = run_experiment(benchmark, tracking=tracking)
    assert first.metadata.config_sha256 == second.metadata.config_sha256
    assert (
        first.metadata.evaluation_conditions["features_sha256"]
        != second.metadata.evaluation_conditions["features_sha256"]
    )
    with pytest.raises(ValueError, match="evaluation_conditions"):
        load_reports(
            json.loads(
                json.dumps(
                    {
                        "first": {"schema_version": 1, **asdict(first)},
                        "second": {"schema_version": 1, **asdict(second)},
                    }
                )
            ),
            dataset_id=first.metadata.dataset_id,
            top_k=20,
        )
    if tracked:
        from mlflow import MlflowClient

        run = MlflowClient(tracking_uri=tracking.tracking_uri).get_run(second.tracking.run_id)
        assert (
            run.data.params["features_sha256"]
            == second.metadata.evaluation_conditions["features_sha256"]
        )


@pytest.mark.parametrize("marker", ["source", "transformation"])
def test_new_feature_export_cannot_silently_fall_back_to_unchecked_legacy(tmp_path, marker):
    from src.infrastructure.datasets.features import load_features, write_features

    path = tmp_path / "features.json"
    raw = write_features(
        path, dataset_id="toy", snapshot_id="s", source={}, features={"a": {"rock": 2}}
    )
    del raw["content_sha256"]
    del raw["transformation" if marker == "source" else "source"]
    raw["features"]["a"]["rock"] = 8
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="checksum"):
        load_features(path, dataset_id="toy", snapshot_id="s")


def test_binary_search_report_itself_retains_feature_identity(study):
    from dataclasses import replace

    from src.infrastructure.datasets.features import write_features
    from src.interfaces.cli.experiment_search import load_study, run_search, smoke
    from src.interfaces.notebook.experiment_analysis import load_reports

    first = model_config(study(), "content")
    raw = json.loads(first.features.read_text())
    changed = first.path.parent / "changed-features.json"
    write_features(
        changed,
        dataset_id=raw["dataset_id"],
        snapshot_id=raw["snapshot_id"],
        source={},
        features={item: {"changed": 1} for item in raw["features"]},
    )
    second = replace(first, features=changed, output=first.output.parent / "other-content")
    reports = {}
    for label, config in [("first", first), ("second", second)]:
        smoke(config)
        run_search(config)
        saved = load_study(config)
        trial = saved["trials"][saved["planned"][0]]
        reports[label] = json.loads((config.output / trial["report"]).read_text())
    with pytest.raises(ValueError, match="evaluation_conditions"):
        load_reports(reports, dataset_id=raw["dataset_id"], top_k=20)


@pytest.mark.parametrize("weight,base_name", [(0.0, "content"), (1.0, "usercf")])
def test_hybrid_endpoints_keep_base_order_when_rrf_denominators_round_equal(weight, base_name):
    events = [Interaction("u", "a"), Interaction("v", "a"), Interaction("v", "b")] + [
        Interaction("w", "c")
    ] * 10
    features = {"a": {"rock": 1}, "b": {"rock": 1}, "c": {"jazz": 1}}
    request = RecommendationRequest("r", "u", ("a",), ("b", "c"), 20)
    base = create_recommender(
        base_name, events, **({"features": features} if base_name == "content" else {})
    )
    hybrid = create_recommender(
        "hybrid",
        events,
        features=features,
        parameters={"cf_model": "usercf", "cf_weight": weight, "rrf_constant": 1e308},
    )
    assert hybrid.recommend(request).item_ids == base.recommend(request).item_ids


@pytest.mark.parametrize(
    "missing,expected", [(True, "features_missing"), (False, "features_zero_norm")]
)
def test_diversity_preserves_feature_exclusion_causes_in_saved_report(tmp_path, missing, expected):
    from src.domain.evaluation.listening_diversity import listening_diversity
    from src.infrastructure.evaluation.listening_benchmark import (
        ListeningReport,
        save_listening_report,
    )

    data, runs, features = fixture(("a", "b"))
    if missing:
        del features["b"]
    else:
        features["b"] = {"jazz": 0}
    beyond = listening_diversity(data, runs, ks=(2,), features=features)
    assert beyond["requests"][0]["ild_exclusion_reasons"] == [expected]
    assert beyond["metrics"][0]["ild_exclusion_counts"] == {expected: 1}
    report = ListeningReport(
        2,
        {
            "model_name": "fixture",
            "n_generation_calls": 1,
            "evaluation_conditions": {
                "phase": "validation",
                "primary": {"window_days": 90, "k": 10, "day_weight": 1},
            },
        },
        (),
        (),
        beyond_accuracy=beyond,
    )
    # The regular runner's save path preserves extra aggregate/detail keys as-is.
    path = save_listening_report(report, tmp_path / "results")
    saved = json.loads((path / "results.json").read_text())
    assert saved["beyond_accuracy"]["requests"][0]["ild_exclusion_reasons"] == [expected]
