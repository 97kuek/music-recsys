import json
import os
import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "gpu",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
            ),
        ),
    ],
)
def test_als_final_test_preserves_validation_and_fixed_factor_comparison(tmp_path, gpu):
    import matplotlib.pyplot as plt

    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.datasets.listening_data import load_listening_dataset
    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark
    from src.infrastructure.evaluation.tracking import TrackingConfig
    from src.infrastructure.recommenders import create_recommender
    from src.infrastructure.research.configuration import load_settings
    from src.interfaces.cli.fixed_test import _plan, run
    from src.interfaces.cli.grid import run_grid
    from src.interfaces.notebook.als_test_figures import figures

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/als.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(bundle / "test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "data/experiments/als"),
    )
    raw["model"]["parameters"]["iterations"] = 2
    raw["search"]["grid"]["factors"] = [2, 4]
    path = tmp_path / "als.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    run_grid(path)
    parent, configs = _plan(path)
    original = {c.output / "result.json": (c.output / "result.json").read_bytes() for c in configs}
    output = run(path, gpu=gpu)
    summary = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert summary["phase"] == "test" and summary["selection"] == "not_selected"
    assert len(summary["sources"]) == 2 and summary["paired_differences"]
    assert original == {p: p.read_bytes() for p in original}
    records = []
    data = load_listening_dataset(parent.test_dataset, parent.evaluation)
    features = load_features(
        parent.features,
        dataset_id=data.dataset_id,
        snapshot_id=data.manifest["source"]["snapshot_id"],
    )
    for c in configs:
        result = json.loads((c.output / "test/result.json").read_text(encoding="utf-8"))
        saved = json.loads((c.output / "test/config.json").read_text(encoding="utf-8"))
        assert saved["execution"]["evaluation_device"] == ("cuda" if gpu else "cpu")
        assert saved["execution"]["training_parameters"]["use_gpu"] is gpu
        assert saved["settings"]["parameters"] == c.parameters
        assert result["primary"]["ks"] == list(range(2, 21))
        records.append(
            dict(
                condition=f"als-{c.parameters['factors']}",
                factors=c.parameters["factors"],
                result=result,
            )
        )
        if not gpu:
            reference = run_listening_benchmark(
                data,
                create_recommender(
                    "als", data.train_interactions, parameters=c.parameters, seed=c.seeds[0]
                ).recommend,
                config=c.evaluation,
                model_name="als",
                features=features,
                tracking=TrackingConfig(enabled=False),
            )

            def key(r):
                return r["task"], r["window_days"], r["day_weight"], r["metric"], r["k"]

            expected = {key(r): r for r in reference.summary}
            for row in result["accuracy"]:
                assert row["mean"] == pytest.approx(expected[key(row)]["value"])
                assert row["n_evaluated"] == expected[key(row)]["n_evaluated"]
    baseline_names = (
        "popularity-plays",
        "popularity-users",
        "itemcf-cosine-20",
        "itemcf-jaccard-100",
    )
    baselines = [
        dict(
            condition=name,
            result=dict(
                records[0]["result"],
                observations=list(reversed(records[0]["result"]["observations"])),
            ),
        )
        for name in baseline_names
    ]
    drawn = figures(records + baselines)
    try:
        assert all(not ax.get_title() for fig in drawn.values() for ax in fig.axes)
        ax = drawn["recall_factors"].axes[0]
        assert list(ax.get_xticks()) == [2, 4] and ax.get_xscale() == "linear"
        assert "@20" in ax.get_ylabel() and "(%)" in ax.get_ylabel()
    finally:
        plt.close("all")
    with pytest.raises(FileExistsError):
        run(path, gpu=gpu)


def test_factor_plan_rejects_changed_hyperparameters_before_scoring(tmp_path):
    from src.infrastructure.research.configuration import load_settings
    from src.infrastructure.research.grid import grid_variants as variants
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli.fixed_test import _plan

    raw = load_settings("configs/als.json")
    raw["experiment"]["output"] = str(tmp_path / "als")
    path = tmp_path / "als.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    parent = load_study_config(path)
    for p, r in variants(parent):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(r), encoding="utf-8")
    bad = load_settings(path)
    bad["search"] = dict(seeds=[0], max_trials=1)
    bad["model"]["parameters"].update(factors=256, alpha=1)
    bad["experiment"]["output"] = str(tmp_path / "als/256")
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="identical ALS settings"):
        _plan(path, extra=[extra])
