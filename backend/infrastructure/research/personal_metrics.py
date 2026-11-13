"""GPU score replay, oracle diagnostics, and user-paired exploratory uncertainty."""

import numpy as np

from backend.infrastructure.research.complementarity import percentile_interval
from backend.infrastructure.research.personal_rules import POLICIES


def interval(values, *, xp, repetitions=2000):
    n = len(values)
    if n < 2:
        return None
    rng = np.random.default_rng(42)
    samples = xp.empty((repetitions, values.shape[1]), dtype=xp.float64)
    for start in range(0, repetitions, 16):
        count = min(16, repetitions - start)
        indices = xp.asarray(rng.integers(0, n, (count, n)))
        samples[start : start + count] = values[indices].mean(axis=1)
    bounds = percentile_interval(samples, xp)
    return xp.asnumpy(bounds) if xp is not np else bounds


def accuracy(blocks, choices, ks, *, xp, population, policies=POLICIES):
    rows, pairs, oracle = [], [], []
    for key, (indices, scores) in blocks.items():
        task, window, weight, metric = key
        task_index = int(task == "repeat")
        values = xp.asarray(scores)
        selected = xp.asarray(choices[task_index][:, indices].T)
        predictions = values[xp.arange(len(indices))[:, None], selected]
        means = predictions.mean(axis=0)
        best = values.max(axis=1)
        best_means = best.mean(axis=0)
        fixed_means = values.mean(axis=0)
        header = dict(
            task=task,
            window_days=int(window),
            day_weight=None if weight < 0 else float(weight),
            metric=metric,
        )
        for p, policy in enumerate(policies):
            for j, k in enumerate(ks):
                rows.append(
                    dict(
                        condition=policy,
                        aspect="accuracy",
                        **header,
                        k=k,
                        mean=float(means[p, j]),
                        n_evaluated=len(indices),
                        n_excluded=population - len(indices),
                    )
                )
        for j, k in enumerate(ks):
            baseline = int(xp.argmax(fixed_means[:, j]))
            oracle.append(
                dict(
                    **header,
                    k=k,
                    mean=float(best_means[j]),
                    best_fixed=policies[baseline],
                    fixed_mean=float(fixed_means[baseline, j]),
                    difference=float(best_means[j] - fixed_means[baseline, j]),
                    improved_user_fraction=float(
                        (best[:, j] > values[:, baseline, j] + 1e-12).mean()
                    ),
                    n_evaluated=len(indices),
                    n_excluded=population - len(indices),
                )
            )
        # Fixed user-seed averages; interval conditions on fitted cross-fold rules.
        if window != 90 or weight not in (-1, 1) or task == "retention_proxy":
            continue
        j = list(ks).index(20)
        comparisons = [(a, b) for b in (5, 6) for a in (0, 1, 2, 3, 4, b + 2)]
        if len(policies) > 9:
            comparisons.extend((a, b) for b in (9, 10) for a in (0, 1, 2, 3, 4, b - 4, b - 2))
        differences = xp.stack(
            [predictions[:, b, j] - predictions[:, a, j] for a, b in comparisons], axis=1
        )
        bounds = interval(differences, xp=xp)
        for i, (a, b) in enumerate(comparisons):
            pairs.append(
                dict(
                    **header,
                    k=20,
                    model_left=policies[a],
                    model_right=policies[b],
                    mean=float(differences[:, i].mean()),
                    ci_low=float(bounds[0, i]) if bounds is not None else None,
                    ci_high=float(bounds[1, i]) if bounds is not None else None,
                    n_evaluated=len(indices),
                )
            )
    return rows, pairs, oracle
