from dataclasses import replace

import pytest
from model_suite_support import MODELS, model_config


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("protocol", ["binary-v1", "listening-v2"])
def test_all_models_complete_common_search_selection_and_final_test(
    study, listening_study, model, protocol
):
    from src.infrastructure.research.search import (
        freeze_selection,
        inspect_selected,
        review_selection,
        run_final_test,
        run_search,
        select_best,
        smoke,
    )
    from src.interfaces.notebook.inspection import inspect_recommendation

    config = model_config(listening_study() if protocol == "listening-v2" else study(), model)
    assert smoke(config)["status"] == "succeeded"
    searched = run_search(config)
    assert searched["status"] == "succeeded"
    assert run_search(config)["reused_trials"] == searched["planned_trials"]
    selected = select_best(config)
    assert selected["model"] == model
    inspection = inspect_selected(config)
    result = inspect_recommendation(
        inspection["dataset"],
        model=model,
        parameters=inspection["parameters"],
        seed=inspection["seed"],
        features=inspection["features"],
        request_id=inspection["dataset"].requests[0].request_id,
    )
    assert result.response.status == "ok"
    assert all(r["reason"] is not None for r in result.recommendations)
    review_selection(config, accepted=True)
    freeze_selection(config)
    assert run_final_test(config)["status"] == "succeeded"
    assert run_final_test(config)["reused"]


def test_diagnostic_results_are_readable_without_selecting_a_best_setting(listening_study):
    from src.infrastructure.research.search import (
        run_final_test,
        run_search,
        select_best,
        smoke,
        trial_rows,
    )
    from src.interfaces.notebook.search_analysis import trial_curves

    config = model_config(listening_study(metric=None), "content")
    smoke(config)
    run_search(config)
    curves = trial_curves(config, trial_rows(config)[0]["trial_id"], resamples=100)
    assert set(curves.task) == {"discovery", "repeat", "retention_proxy"}
    assert curves.novelty.notna().any()
    with pytest.raises(ValueError, match="主指標"):
        select_best(config)
    with pytest.raises(ValueError):
        run_final_test(config)
    assert not (config.output / "selection.json").exists()


def test_changed_or_wrong_source_features_cannot_reuse_a_successful_search(
    listening_study, tmp_path
):
    import json

    from src.infrastructure.research.search import load_study, run_search, smoke

    config = model_config(listening_study(), "content")
    smoke(config)
    run_search(config)
    raw = json.loads(config.features.read_text())
    raw["snapshot_id"] = "different"
    other = tmp_path / "other.json"
    other.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_study(replace(config, features=other))


@pytest.mark.parametrize("protocol", ["binary-v1", "listening-v2"])
def test_standalone_benchmark_loads_content_features_and_rejects_wrong_source(
    study, listening_study, protocol
):
    from src.infrastructure.datasets.features import write_features
    from src.infrastructure.evaluation.benchmark import run_experiment

    config = model_config(listening_study() if protocol == "listening-v2" else study(), "content")
    path = config.path.parent / "benchmark.toml"
    evaluation = "\n[evaluation]\nwindow_days=[30,90,180]\n" if protocol == "listening-v2" else ""
    path.write_text(
        f'[benchmark]\nprotocol="{protocol}"\ndataset="{config.validation_dataset}"\nfeatures="{config.features}"\nmodel="content"\ntop_k=20\nseed=42\n'
        + evaluation
    )
    report = run_experiment(path)
    if protocol == "listening-v2":
        assert report.metadata["n_failed"] == 0
        assert report.metadata["evaluation_conditions"]["features_sha256"] is not None
    else:
        assert report.summary.n_failed == 0
    bad = config.path.parent / "wrong-features.json"
    write_features(bad, dataset_id="other", snapshot_id="other", features={}, source={})
    path.write_text(path.read_text().replace(str(config.features), str(bad)))
    with pytest.raises(ValueError, match="出典"):
        run_experiment(path)


def test_recall_objective_keeps_its_label_in_tradeoff_analysis(study):
    from src.infrastructure.research.search import run_search, smoke
    from src.interfaces.notebook.search_analysis import tradeoff_points

    config = replace(model_config(study(), "usercf"), metric="recall")
    smoke(config)
    run_search(config)
    frame = tradeoff_points(config)
    assert frame.attrs["accuracy_metric"] == "recall"
    assert frame.accuracy.notna().all()


def test_legacy_v2_without_beyond_accuracy_remains_unmeasured(listening_study):
    from src.infrastructure.research.search import run_search, smoke, trial_rows
    from src.interfaces.notebook.search_analysis import _curve_frame, _trial_bundles

    config = model_config(listening_study(), "usercf")
    smoke(config)
    run_search(config)
    bundles = _trial_bundles(config, [trial_rows(config)[0]["trial_id"]])
    for bundle in bundles:
        bundle.pop("beyond_accuracy")
    frame = _curve_frame(bundles, "usercf", "validation", 100)
    assert frame.ild.isna().all() and frame.novelty.isna().all()
