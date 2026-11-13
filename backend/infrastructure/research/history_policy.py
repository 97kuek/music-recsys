"""Two-candidate, one-split history policy with user-disjoint fitting."""

import numpy as np

FEATURES = ("tracks", "plays", "item_days", "top_share")
POLICIES = (
    "last-play",
    "recency-log-count",
    "rule-ndcg",
    "rule-recall",
    "train-fixed-ndcg",
    "train-fixed-recall",
)


def fit(x, y, *, xp, minimum_leaf=200):
    valid = xp.isfinite(y).all(axis=1)
    if not bool(valid.any()):
        raise ValueError("Eligible training outcomes required")
    target = int(xp.argmax(y[valid].mean(axis=0)))
    best = float(y[valid, target].mean())
    rule = dict(feature=None, threshold=None, left=target, right=target)
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
                rule = dict(feature=feature, threshold=float(threshold), left=a, right=b)
    return rule, target


def predict(rule, x):
    if rule["feature"] is None:
        return np.full(len(x), rule["left"], dtype=np.int8)
    return np.where(
        x[:, FEATURES.index(rule["feature"])] <= rule["threshold"], rule["left"], rule["right"]
    ).astype(np.int8)


def crossfit(x, outcomes, folds, *, xp, minimum_leaf=200):
    if outcomes.shape != (len(x), 2) or set(np.unique(folds)) != {0, 1}:
        raise ValueError("Two aligned candidates and two user folds required")
    selected, constant = np.empty(len(x), np.int8), np.empty(len(x), np.int8)
    records = []
    for held in (0, 1):
        train = folds != held
        rule, target = fit(x[train], outcomes[xp.asarray(train)], xp=xp, minimum_leaf=minimum_leaf)
        selected[~train] = predict(rule, x[~train])
        constant[~train] = target
        records.append(
            dict(
                held_fold=held,
                training_users=int(train.sum()),
                evaluation_users=int((~train).sum()),
                rule=rule,
                constant=target,
            )
        )
    return selected, constant, records
