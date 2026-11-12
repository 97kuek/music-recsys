"""GPU ranked Recall by training-only strata, with fixed-seed paired intervals."""

import numpy as np

from backend.infrastructure.research.complementarity import percentile_interval

CONDITIONS = ("ease-union", "rrf", "rrf-bpr-only")


def ranked_recall(rankings, truths, popularity, added, ks, *, xp):
    """Band-specific truth denominators; added hits retain their original RRF positions."""
    width = max(1, max(map(len, truths), default=0))
    truth = np.full((len(truths), width), -1, dtype=np.int32)
    for i, ids in enumerate(truths):
        truth[i, : len(ids)] = ids
    truth = xp.asarray(truth)
    bands = xp.asarray(popularity)[xp.maximum(truth, 0)]
    positions = xp.asarray(ks) - 1
    for band in (None, 0, 1, 2, 3):
        valid = truth >= 0
        if band is not None:
            valid &= bands == band
        denominator = valid.sum(axis=1)
        hits = [
            ((ranking[:, :, None] == truth[:, None, :]) & valid[:, None, :]).any(axis=2)
            & (ranking >= 0)
            for ranking in rankings
        ]
        hits.append(hits[1] & added)
        values = xp.stack(
            [
                hit.cumsum(axis=1)[:, positions] / xp.maximum(denominator[:, None], 1)
                for hit in hits
            ],
            axis=1,
        )
        yield band, denominator > 0, values


def summarize(seeds, history, ks, *, xp, bootstrap=2000):
    """Average seeds per user; bootstrap paired users only at 90 days / K20."""
    if any(set(seed) != set(seeds[0]) for seed in seeds):
        raise ValueError("Seed strata differ")
    rows = []
    for key, (users, _) in seeds[0].items():
        task, window, kind, band = key
        if any(seed[key][0] != users for seed in seeds):
            raise ValueError("Seed eligible users differ")
        population = (
            int((history == int(band[1:]) - 1).sum()) if kind == "history" else len(history)
        )
        n = len(users)
        values = xp.stack([xp.asarray(seed[key][1]) for seed in seeds]).mean(axis=0)
        values = xp.concatenate((values, (values[:, 1] - values[:, 0])[:, None, :]), axis=1)
        means = values.mean(axis=0) if n else None
        interval = None
        if window == 90 and 20 in ks and n >= 2:
            point = values[:, :, list(ks).index(20)]
            samples = xp.empty((bootstrap, 4), dtype=xp.float64)
            rng = np.random.default_rng(42)
            for start in range(0, bootstrap, 16):
                count = min(16, bootstrap - start)
                indices = xp.asarray(rng.integers(0, n, (count, n)))
                samples[start : start + count] = point[indices].mean(axis=1)
            interval = percentile_interval(samples, xp)
        for i, condition in enumerate((*CONDITIONS, "rrf-minus-ease")):
            for j, k in enumerate(ks):
                rows.append(
                    dict(
                        condition=condition,
                        task=task,
                        window_days=window,
                        stratum_kind=kind,
                        stratum=band,
                        k=k,
                        metric="recall_difference" if i == 3 else "recall",
                        mean=float(means[i, j]) if n else None,
                        ci_low=float(interval[0, i]) if interval is not None and k == 20 else None,
                        ci_high=float(interval[1, i]) if interval is not None and k == 20 else None,
                        n_population=population,
                        n_evaluated=n,
                        n_excluded=population - n,
                        seeds=len(seeds),
                    )
                )
    return rows
