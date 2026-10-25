import os
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest


def test_binary_pearson_matches_correlation_and_excludes_constant_columns():
    from src.infrastructure.recommenders.binary_similarity import pearson

    rng = np.random.default_rng(83)
    values = rng.integers(0, 2, (31, 9))
    shared = values.T @ values
    counts = values.sum(axis=0)
    np.testing.assert_allclose(
        pearson(shared, counts[:, None], counts[None, :], len(values)),
        np.corrcoef(values.T),
        atol=1e-14,
    )
    assert np.isnan(float(pearson(3, 3, 31, 31)))
    assert np.isnan(float(pearson(0, 0, 3, 31)))
    assert float(pearson(0, 4, 5, 31)) < 0


def test_itemcf_pearson_has_no_edges_for_universal_item_and_drops_negative_edges():
    from src.domain.evaluation.dataset import Interaction
    from src.infrastructure.recommenders.itemcf import ItemCFRecommender

    rows = [Interaction(f"u{i}", "all") for i in range(6)]
    rows += [
        Interaction(f"u{i}", item)
        for item, users in (("a", [0, 1]), ("b", [0, 1, 2]), ("c", [3, 4]))
        for i in users
    ]
    model = ItemCFRecommender(rows, neighbors=20, similarity="pearson")
    assert model._neighbors["all"] == {}
    assert set(model._neighbors["a"]) == {"b"}
    assert model._neighbors["a"]["b"] == pytest.approx(
        np.corrcoef([1, 1, 0, 0, 0, 0], [1, 1, 1, 0, 0, 0])[0, 1]
    )


def test_heatmap_discrete_cells_keep_measured_zero_and_missing():
    import matplotlib.pyplot as plt

    from src.interfaces.notebook.itemcf_heatmaps import heatmaps

    frame = pd.DataFrame(
        [
            dict(
                similarity=s, neighbors=n, task=task, metric=metric, k=20, window_days=90, mean=mean
            )
            for task, metric in (("discovery", "recall"), ("repeat", "repeat_recall"))
            for s, n, mean in (
                ("cosine", 5, 0),
                ("cosine", 20, 0.01),
                ("pearson", 5, None),
                ("pearson", 20, 0.02),
            )
        ]
    )
    drawn = heatmaps(frame, ["cosine", "pearson"], [5, 20])
    try:
        assert set(drawn) == {"recall", "repeat_recall"}
        ax = drawn["recall"].axes[0]
        assert not ax.get_title() and ax.images[0].get_interpolation() == "nearest"
        assert ax.images[0].get_array()[0, 0] == 0
        assert ax.images[0].get_array().mask[1, 0]
        assert [x.get_text() for x in ax.get_xticklabels()] == ["5", "20"]
        assert heatmaps(frame.assign(mean=None), ["cosine", "pearson"], [5, 20]) == {}
    finally:
        plt.close("all")


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_GPU") != "1", reason="explicit GPU test required"
)
@pytest.mark.parametrize("similarity", ["cosine", "jaccard", "pearson"])
def test_prefix_scoring_matches_cpu_including_short_histories_and_zero_neighbors(
    tmp_path, similarity
):
    import runpy

    import cupy as cp

    from src.infrastructure.datasets.features import load_features
    from src.infrastructure.datasets.listening_data import validate_bundle
    from src.infrastructure.evaluation.full_scoring import score_cpu
    from src.infrastructure.evaluation.itemcf_sensitivity import prefix_models, score_prefixes
    from src.infrastructure.recommenders.full_itemcf import FullItemCF
    from src.infrastructure.research.configuration import load_settings
    from src.infrastructure.research.search import load_study_config
    from src.infrastructure.research.store import atomic_json

    create = runpy.run_path("tests/fixtures/examples/toy/create_listening.py")["create_example"]
    bundle = create(tmp_path / "bundle")
    raw = load_settings("configs/itemcf-sensitivity.json")
    raw["experiment"].update(
        validation_dataset=str(bundle / "validation"),
        test_dataset=str(bundle / "test"),
        features=str(bundle / "features.json"),
        output=str(tmp_path / "outputs"),
    )
    path = tmp_path / "study.json"
    atomic_json(path, raw)
    parent = load_study_config(path)
    manifest, paths, counts = validate_bundle(parent.validation_dataset)
    features = load_features(
        parent.features,
        dataset_id=manifest["dataset_id"],
        snapshot_id=manifest["source"]["snapshot_id"],
    )
    base = FullItemCF(
        paths["learning"],
        similarity=similarity,
        neighbors=50,
        use_gpu=True,
        progress=lambda _: None,
    )
    targets, weights = base.targets.copy(), base.weights.copy()
    models = prefix_models(base, [1, 2, 50])
    np.testing.assert_array_equal(base.targets, targets)
    np.testing.assert_array_equal(base.weights, weights)
    configs = [
        replace(
            parent,
            output=tmp_path / str(n),
            grid={},
            parameters=dict(similarity=similarity, neighbors=n),
        )
        for n in (1, 2, 50)
    ]
    for config in configs:
        config.output.mkdir()
    users = dict(
        zip(
            base.items,
            np.asarray(base.histories.sum(axis=0)).ravel().astype(int).tolist(),
            strict=True,
        )
    )
    actual = score_prefixes(configs, models, paths, counts, features, users, lambda _: None)
    popularity = sorted(base.items, key=lambda i: (-base.plays[base.index[i]], i))
    for config, model, values in zip(configs, models, actual, strict=True):
        reference = replace(config, output=config.output / "reference")
        reference.output.mkdir()
        expected = score_cpu(
            reference, paths, counts, features, users, popularity, {}, None, model, lambda _: None
        )

        def key(r):
            return r["task"], r["metric"], r["window_days"], r["day_weight"] or -1, r["k"]

        left, right = sorted(values[0], key=key), sorted(expected[0], key=key)
        for a, b in zip(left, right, strict=True):
            assert a["mean"] == pytest.approx(b["mean"])
            assert a["n_evaluated"] == b["n_evaluated"]
        for a, b in zip(values[1], expected[1], strict=True):
            for metric in ("ild", "novelty", "catalog_coverage"):
                assert a[metric] == pytest.approx(b[metric])
                if metric != "catalog_coverage":
                    assert a[metric + "_evaluated"] == b[metric + "_evaluated"]
    cp.get_default_memory_pool().free_all_blocks()
