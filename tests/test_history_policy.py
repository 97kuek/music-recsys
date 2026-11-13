import numpy as np

from backend.infrastructure.research.history_policy import crossfit, fit, predict


def test_held_out_labels_do_not_change_their_predictions_or_thresholds():
    x = np.repeat(np.arange(32, dtype=float)[:, None], 4, axis=1)
    folds = np.arange(32) % 2
    y = np.column_stack((x[:, 0] < 16, x[:, 0] >= 16)).astype(float)
    selected, _, rules = crossfit(x, y, folds, xp=np, minimum_leaf=2)
    changed = y.copy()
    changed[folds == 0] = changed[folds == 0, ::-1] * 100
    other, _, other_rules = crossfit(x, changed, folds, xp=np, minimum_leaf=2)
    np.testing.assert_array_equal(selected[folds == 0], other[folds == 0])
    assert rules[0] == other_rules[0]
    assert len(set(selected)) == 2


def test_constant_control_and_missing_outcomes():
    x = np.repeat(np.arange(20, dtype=float)[:, None], 4, axis=1)
    y = np.tile([0.2, 0.4], (20, 1))
    y[0] = np.nan
    rule, target = fit(x, y, xp=np, minimum_leaf=2)
    assert target == 1 and rule["feature"] is None
    np.testing.assert_array_equal(predict(rule, x), np.ones(20))
