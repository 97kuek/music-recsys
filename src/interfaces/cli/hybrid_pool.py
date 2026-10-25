"""Fixed-seed user bootstrap for candidate Recall and matched union increments."""

import numpy as np


def summarize(results, *, cp, population):
    import pyarrow.parquet as pq

    from src.infrastructure.research.complementarity import percentile_interval

    groups = {r["condition"]["group"] for r in results if r["pool_recall"]}
    rows = []
    labels = ("source_a100", "source_b100", "union100", "source_a200", "source_b200")
    for group in sorted(groups):
        sources = [r for r in results if r["condition"]["group"] == group]
        for task in ("new", "repeat"):
            for window in (30, 90, 180):
                user_ids = None
                seeds = []
                for source in sources:
                    table = pq.read_table(
                        str(source["directory"]) + "/pool.parquet",
                        filters=[("task", "=", task), ("window_days", "=", window)],
                    ).sort_by([("user_id", "ascending")])
                    users = table["user_id"].to_pylist()
                    if user_ids is not None and user_ids != users:
                        raise ValueError("Candidate Recall seed users differ")
                    user_ids = users
                    seeds.append(
                        cp.asarray(
                            np.asarray(table["values"].to_pylist(), dtype=float).reshape(-1, 5)
                        )
                    )
                values = cp.stack(seeds).mean(axis=0)
                n = len(values)
                means = values.mean(axis=0) if n else None
                sampled = None
                if window == 90 and n >= 2:
                    sampled = cp.empty((2000, 5), dtype=cp.float64)
                    rng = np.random.default_rng(42)
                    for start in range(0, 2000, 16):
                        size = min(16, 2000 - start)
                        indices = cp.asarray(rng.integers(0, n, (size, n)))
                        sampled[start : start + size] = values[indices].mean(axis=1)
                interval = percentile_interval(sampled, cp) if sampled is not None else None
                for i, label in enumerate(labels):
                    rows.append(
                        dict(
                            group=group,
                            task=task,
                            window_days=window,
                            scope=label,
                            mean=float(means[i]) if n else None,
                            n_evaluated=n,
                            n_excluded=population - n,
                            ci_low=float(interval[0, i]) if interval is not None else None,
                            ci_high=float(interval[1, i]) if interval is not None else None,
                        )
                    )
                for i, label in enumerate(labels):
                    if i == 2:
                        continue
                    difference = (
                        percentile_interval(sampled[:, 2] - sampled[:, i], cp)
                        if sampled is not None
                        else None
                    )
                    rows.append(
                        dict(
                            group=group,
                            task=task,
                            window_days=window,
                            scope="union_minus_" + label,
                            mean=float(means[2] - means[i]) if n else None,
                            n_evaluated=n,
                            n_excluded=population - n,
                            ci_low=float(difference[0]) if difference is not None else None,
                            ci_high=float(difference[1]) if difference is not None else None,
                        )
                    )
    return rows
