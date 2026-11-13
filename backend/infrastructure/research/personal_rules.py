"""Exploratory, user-disjoint cross-fitting of shallow selection rules."""

from dataclasses import asdict

import numpy as np

from backend.domain.recommendation.local_selector import TARGETS
from backend.domain.recommendation.personal_selector import FEATURES, PersonalPolicy

POLICIES = (
    *TARGETS,
    "task-rule",
    "rule-ndcg",
    "rule-recall",
    "train-fixed-ndcg",
    "train-fixed-recall",
)


def fit_rule(x, y, *, xp, minimum_leaf=200):
    """Missing outcomes remain NaN. Quartiles use training users only."""
    valid = xp.isfinite(y).all(axis=1)
    if not bool(valid.any()):
        raise ValueError("No eligible training users")
    target = int(xp.argmax(y[valid].mean(axis=0)))
    best = float(y[valid, target].mean())
    constant = PersonalPolicy(None, None, TARGETS[target], TARGETS[target])
    rule = constant
    for j, feature in enumerate(FEATURES):
        for threshold in np.unique(np.quantile(x[:, j], [0.25, 0.5, 0.75], method="inverted_cdf")):
            below = xp.asarray(x[:, j] <= threshold)
            left, right = valid & below, valid & ~below
            if min(int(left.sum()), int(right.sum())) < minimum_leaf:
                continue
            a, b = int(xp.argmax(y[left].mean(axis=0))), int(xp.argmax(y[right].mean(axis=0)))
            value = float((y[left, a].sum() + y[right, b].sum()) / valid.sum())
            if value > best + 1e-12:
                best = value
                rule = PersonalPolicy(feature, float(threshold), TARGETS[a], TARGETS[b])
    return rule, constant, best


def predict(rule, x):
    if rule.feature is None:
        return np.full(len(x), TARGETS.index(rule.left), dtype=np.int8)
    return np.where(
        x[:, FEATURES.index(rule.feature)] <= rule.threshold,
        TARGETS.index(rule.left),
        TARGETS.index(rule.right),
    ).astype(np.int8)


def crossfit(x, outcomes, folds, *, xp, minimum_leaf=200):
    """Every prediction uses a rule fitted without that user's outcome."""
    if set(np.unique(folds)) != {0, 1} or outcomes.shape != (len(x), 4):
        raise ValueError("Two nonempty folds and four aligned targets required")
    predictions, constants = np.empty(len(x), np.int8), np.empty(len(x), np.int8)
    rules = []
    for held in (0, 1):
        train, test = folds != held, folds == held
        rule, constant, mean = fit_rule(
            x[train], outcomes[xp.asarray(train)], xp=xp, minimum_leaf=minimum_leaf
        )
        predictions[test], constants[test] = predict(rule, x[test]), predict(constant, x[test])
        rules.append(
            dict(
                held_fold=held,
                training_users=int(train.sum()),
                evaluation_users=int(test.sum()),
                rule=asdict(rule),
                constant=asdict(constant),
                training_objective_mean=mean,
            )
        )
    return predictions, constants, rules
