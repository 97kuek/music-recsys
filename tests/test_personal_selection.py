"""Future-label isolation, honest oracle bounds, and selected-ranking reaggregation."""

import os
from dataclasses import asdict

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.domain.recommendation.personal_selector import FEATURES, PersonalPolicy
from backend.domain.recommendation.selection_context import SelectionContext
from backend.infrastructure.research.personal_diversity import diversity
from backend.infrastructure.research.personal_metrics import accuracy
from backend.infrastructure.research.personal_rules import POLICIES, crossfit, predict
from backend.infrastructure.research.personal_tree import crossfit_tree, fit_tree, predict_tree


def test_tree_is_shallow_and_held_labels_do_not_change_predictions():
    x = np.zeros((80, 6))
    x[:, 0] = np.arange(80)
    y = np.eye(4)[np.arange(80) // 20]
    folds = np.arange(80) % 2
    selected, records = crossfit_tree(x, y, folds, xp=np, minimum_leaf=4)
    changed = y.copy()
    changed[folds == 0] = [0, 0, 0, 100]
    other, _ = crossfit_tree(x, changed, folds, xp=np, minimum_leaf=4)
    np.testing.assert_array_equal(selected[folds == 0], other[folds == 0])
    stump = fit_tree(x, y, xp=np, depth=1, minimum_leaf=4)
    tree = fit_tree(x, y, xp=np, depth=2, minimum_leaf=4)
    assert (
        y[np.arange(80), predict_tree(tree, x)].mean()
        > y[np.arange(80), predict_tree(stump, x)].mean()
    )
    for record in records:
        for child in record["tree"].get("children", []):
            assert "children" not in child
    with pytest.raises(ValueError, match="depth"):
        fit_tree(x, y, xp=np, depth=3)


def test_crossfit_never_uses_held_users_labels():
    x = np.zeros((16, 6))
    x[:, 0] = np.arange(16)
    folds = np.arange(16) % 2
    y = np.zeros((16, 4))
    y[:8, 0], y[8:, 1] = 1, 1
    selected, constant, rules = crossfit(x, y, folds, xp=np, minimum_leaf=2)
    changed = y.copy()
    changed[folds == 0] = [0, 0, 0, 100]
    other, other_constant, _ = crossfit(x, changed, folds, xp=np, minimum_leaf=2)
    np.testing.assert_array_equal(selected[folds == 0], other[folds == 0])
    np.testing.assert_array_equal(constant[folds == 0], other_constant[folds == 0])
    assert len(set(selected)) == 2
    for r in rules:
        rule = PersonalPolicy(**r["rule"])
        assert rule.feature == "history_count"


def test_missing_truth_is_not_a_zero_label_and_domain_matches_vectorization():
    context = SelectionContext("new", 10, 100, 20, 15, 0.3, 0.9)
    x = np.array([[asdict(context)[f] for f in FEATURES]])
    rule = PersonalPolicy("history_count", 10, POLICIES[0], POLICIES[1])
    assert rule.choose(context) == POLICIES[int(predict(rule, x)[0])]
    with pytest.raises(ValueError, match="No eligible"):
        crossfit(np.tile(x, (4, 1)), np.full((4, 4), np.nan), np.array([0, 1, 0, 1]), xp=np)


def test_oracle_and_accuracy_use_user_seed_averages_with_explicit_denominator():
    values = np.array(
        [
            [[0.2, 0.3], [0.7, 0.8], [0.4, 0.5], [0.1, 0.1]],
            [[0.6, 0.7], [0.3, 0.4], [0.2, 0.3], [0.1, 0.1]],
        ]
    )
    choices = np.zeros((2, len(POLICIES), 3), np.int8)
    choices[:, :4] = np.arange(4)[None, :, None]
    choices[0, 5] = [1, 0, 0]
    rows, _, oracle = accuracy(
        {("discovery", 30, -1.0, "recall"): (np.array([0, 2]), values)},
        choices,
        [2, 20],
        xp=np,
        population=3,
    )
    row = next(r for r in rows if r["condition"] == "rule-ndcg" and r["k"] == 20)
    assert row["mean"] == pytest.approx(0.75)
    assert row["n_evaluated"] == 2 and row["n_excluded"] == 1
    assert oracle[1]["mean"] == pytest.approx(0.75)
    assert oracle[1]["fixed_mean"] == pytest.approx(0.6)


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
def test_diversity_recomputes_union_instead_of_averaging_source_coverage(tmp_path, gpu):
    if gpu:
        import cupy as xp
    else:
        xp = np
    users, items = ["a", "b"], ["a", "b", "c", "d"]
    candidates = {}
    for model, target in enumerate(POLICIES[:4]):
        rows = [
            dict(
                user_id=u,
                task=t,
                item_ids=items[:2] if model == 0 else items[2:],
                candidate_count=2,
            )
            for u in users
            for t in ("new", "repeat")
        ]
        path = tmp_path / f"{target}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        candidates[target] = dict(
            runs=[
                dict(
                    condition=dict(name=target),
                    artifacts=dict(recommendations=dict(path=str(path))),
                )
            ]
        )
    choices = np.zeros((2, len(POLICIES), 2), np.int8)
    choices[:, :4] = np.arange(4)[None, :, None]
    choices[:, 5] = [0, 1]
    catalog = pa.table(dict(item_id=items, users=[1, 1, 1, 1]))
    features = {i: {i: 1.0} for i in items}
    rows, common = diversity(
        candidates, users, choices, catalog, features, [2], xp=xp, progress=lambda _: None
    )
    values = {
        r["condition"]: r["mean"]
        for r in rows
        if r["task"] == "new" and r["metric"] == "catalog_coverage"
    }
    assert values["ease"] == 0.5
    assert values["rule-ndcg"] == 1.0
    assert all(r["mean"] == pytest.approx(1.0) for r in common)
