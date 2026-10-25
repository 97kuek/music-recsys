import json
import runpy
from pathlib import Path

import pytest


def test_itemcf_final_test_matches_reference_preserves_validation_and_plots(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt

    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.datasets.listening_data import load_listening_dataset
    from src.infrastructure.evaluation.listening_benchmark import run_listening_benchmark
    from src.infrastructure.evaluation.tracking import TrackingConfig
    from src.infrastructure.recommenders import create_recommender
    from src.infrastructure.research.configuration import load_settings
    from src.infrastructure.research.grid import grid_variants as variants
    from src.infrastructure.research.search import load_study_config
    from src.interfaces.cli import evaluate, itemcf_test
    from src.interfaces.cli.grid import run_grid
    from src.interfaces.notebook.itemcf_test_figures import figures

    create = runpy.run_path(
        str(Path(__file__).parent / "fixtures/examples/toy/create_listening.py")
    )["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/itemcf.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(bundle / "test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "data/experiments/itemcf"),
    )
    raw["search"]["grid"] = dict(similarity=["cosine", "jaccard"], neighbors=[1, 2])
    path = tmp_path / "itemcf.json"
    path.write_text(json.dumps(raw))
    run_grid(path)
    parent = load_study_config(path)
    configs = [load_study_config(p) for p, _ in variants(parent)]
    validation = {
        c.output / "result.json": (c.output / "result.json").read_bytes() for c in configs
    }
    output = itemcf_test.run(path, gpu=False)
    summary = json.loads((output / "result.json").read_text())
    assert summary["phase"] == "test" and summary["selection"] == "not_selected"
    assert len(summary["sources"]) == 4
    assert summary["paired_differences"]
    assert validation == {p: p.read_bytes() for p in validation}
    records = []
    data = load_listening_dataset(parent.test_dataset, parent.evaluation)
    features = load_features(
        parent.features,
        dataset_id=data.dataset_id,
        snapshot_id=data.manifest["source"]["snapshot_id"],
    )
    for config in configs:
        result = json.loads((config.output / "test/result.json").read_text())
        reference = run_listening_benchmark(
            data,
            create_recommender(
                "itemcf", data.train_interactions, parameters=config.parameters
            ).recommend,
            config=config.evaluation,
            model_name="itemcf",
            features=features,
            tracking=TrackingConfig(enabled=False),
        )

        def key(r):
            return (r["task"], r["window_days"], r["day_weight"], r["metric"], r["k"])

        expected = {key(r): r for r in reference.summary}
        for row in result["accuracy"]:
            assert row["mean"] == pytest.approx(expected[key(row)]["value"])
            assert row["n_evaluated"] == expected[key(row)]["n_evaluated"]
        records.append(
            dict(condition=itemcf_test._name(config), **config.parameters, result=result)
        )
    # Historical reports may serialize the same measured bins in another order.
    baselines = [
        dict(
            condition=f"popularity-{name}",
            result=dict(
                records[0]["result"],
                observations=list(reversed(records[0]["result"]["observations"])),
            ),
        )
        for name in ("plays", "users")
    ]
    drawn = figures(records + baselines)
    try:
        assert all(not ax.get_title() for fig in drawn.values() for ax in fig.axes)
        ax = drawn["recall_neighbors"].axes[0]
        assert list(ax.get_xticks()) == [1, 2]
        assert "@20" in ax.get_ylabel() and "(%)" in ax.get_ylabel()
        assert (output / "recall_neighbors.png").exists()
        assert (output / "graded_ndcg_repeat_neighbors.png").exists()
    finally:
        plt.close("all")
    with pytest.raises(FileExistsError):
        itemcf_test.run(path, gpu=False)
    with pytest.raises(FileExistsError):
        itemcf_test.finish(path)

    def forbidden(*args, **kwargs):
        pytest.fail("A saved final Test must not be rescored")

    monkeypatch.setattr(evaluate, "run", forbidden)
    # Removing only aggregate output markers permits read-only re-aggregation.
    (output / "result.json").unlink()
    (output / "metrics.csv").unlink()
    itemcf_test.finish(path)
    assert validation == {p: p.read_bytes() for p in validation}
