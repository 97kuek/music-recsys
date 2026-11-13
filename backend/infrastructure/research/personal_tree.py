"""Depth-two greedy policy tree; optimize training utility, not oracle label accuracy."""

from dataclasses import asdict

import numpy as np

from backend.domain.recommendation.personal_selector import FEATURES, PersonalPolicy
from backend.infrastructure.research.personal_rules import fit_rule, predict


def fit_tree(x, outcomes, *, xp, depth=2, minimum_leaf=200):
    if depth < 1 or depth > 2:
        raise ValueError("Predeclared depth must be one or two")
    rule, _, _ = fit_rule(x, outcomes, xp=xp, minimum_leaf=minimum_leaf)
    tree = dict(rule=asdict(rule))
    if depth == 1 or rule.feature is None:
        return tree
    mask = x[:, FEATURES.index(rule.feature)] <= rule.threshold
    tree["children"] = [
        fit_tree(x[m], outcomes[xp.asarray(m)], xp=xp, depth=depth - 1, minimum_leaf=minimum_leaf)
        for m in (mask, ~mask)
    ]
    return tree


def predict_tree(tree, x):
    rule = PersonalPolicy(**tree["rule"])
    if "children" not in tree:
        return predict(rule, x)
    mask = x[:, FEATURES.index(rule.feature)] <= rule.threshold
    selected = np.empty(len(x), dtype=np.int8)
    for child, region in zip(tree["children"], (mask, ~mask), strict=True):
        selected[region] = predict_tree(child, x[region])
    return selected


def crossfit_tree(x, outcomes, folds, *, xp, minimum_leaf=200):
    if set(np.unique(folds)) != {0, 1} or outcomes.shape != (len(x), 4):
        raise ValueError("Two folds and four aligned outcomes required")
    selected = np.empty(len(x), dtype=np.int8)
    records = []
    for held in (0, 1):
        training = folds != held
        tree = fit_tree(
            x[training], outcomes[xp.asarray(training)], xp=xp, minimum_leaf=minimum_leaf
        )
        selected[~training] = predict_tree(tree, x[~training])
        records.append(dict(held_fold=held, tree=tree))
    return selected, records
