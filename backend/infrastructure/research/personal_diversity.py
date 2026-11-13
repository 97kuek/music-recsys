"""Reaggregate selected rankings; Coverage is never an average of source coverages."""

import numpy as np

from backend.infrastructure.evaluation.features import FeaturePrefixes
from backend.infrastructure.research.personal_rules import POLICIES


def ranking_batches(sources, batch_users=256):
    """Sort in RAM: do not spill ten ranking tables onto a nearly-full data disk."""
    import pyarrow.parquet as pq

    tables = [
        pq.read_table(
            s["path"], columns=["user_id", "task", "item_ids", "candidate_count"]
        ).sort_by([("user_id", "ascending"), ("task", "ascending")])
        for s in sources.values()
    ]
    if len({t.num_rows for t in tables}) != 1 or tables[0].num_rows % 2:
        raise ValueError("Source ranking populations differ")
    for start in range(0, tables[0].num_rows, 2 * batch_users):
        yield list(zip(*(t.slice(start, 2 * batch_users).to_pylist() for t in tables), strict=True))


def diversity(
    candidates, users, choices, catalog, features, ks, *, xp, progress, policies=POLICIES
):
    sources, positions = {}, []
    for candidate in candidates.values():
        current = []
        for run in candidate["runs"]:
            current.append(len(sources))
            sources[run["condition"]["name"]] = dict(
                path=run["artifacts"]["recommendations"]["path"]
            )
        positions.append(current if len(current) == 3 else current * 3)
    positions = xp.asarray(positions)
    items = catalog["item_id"].to_pylist()
    lookup = {item: i for i, item in enumerate(items)}
    prefix = FeaturePrefixes(features, items, xp)
    novelty = -xp.log2((xp.asarray(catalog["users"].to_numpy()) + 1) / (len(users) + 2))
    shape = (2, len(policies), 3, len(ks))
    own_sum, common_sum, novel_sum = (xp.zeros(shape, dtype=xp.float64) for _ in range(3))
    own_count, common_count, novel_count = (xp.zeros(shape, dtype=xp.int64) for _ in range(3))
    first_rank = xp.full((2, len(policies), 3, len(items)), 21, dtype=xp.int32)
    cursor = 0
    kpos = xp.asarray(ks) - 1
    for batch in ranking_batches(sources):
        arrays = np.full((len(batch), len(sources), 20), -1, dtype=np.int32)
        for i, request in enumerate(batch):
            expected_user, expected_task = (
                users[(cursor + i) // 2],
                ("new", "repeat")[(cursor + i) % 2],
            )
            if any(r["user_id"] != expected_user or r["task"] != expected_task for r in request):
                raise ValueError("Ranking/context population or ordering differs")
            for j, row in enumerate(request):
                if len(row["item_ids"]) > 20 or len(set(row["item_ids"])) != len(row["item_ids"]):
                    raise ValueError("Invalid saved ranking")
                if len(row["item_ids"]) != min(20, row["candidate_count"]):
                    raise ValueError("Incomplete saved ranking")
                arrays[i, j, : len(row["item_ids"])] = [lookup[x] for x in row["item_ids"]]
        rankings = xp.asarray(arrays)
        ild, valid = prefix.calculate(rankings)
        ild, valid = ild[:, :, kpos], valid[:, :, kpos]
        complete = valid.all(axis=1)
        present = rankings >= 0
        counts = present.cumsum(axis=2)[:, :, kpos]
        novelty_values = (novelty[xp.maximum(rankings, 0)] * present).cumsum(axis=2)[
            :, :, kpos
        ] / xp.maximum(counts, 1)
        start, stop = cursor // 2, (cursor + len(batch)) // 2
        for task in (0, 1):
            selected = positions[xp.asarray(choices[task, :, start:stop].T)]
            batch_index = xp.arange(stop - start)[:, None, None]
            values = ild[task::2][batch_index, selected]
            own = valid[task::2][batch_index, selected]
            common = xp.broadcast_to(complete[task::2, None, None, :], own.shape)
            nov = novelty_values[task::2][batch_index, selected]
            nov_valid = counts[task::2][batch_index, selected] > 0
            own_sum[task] += xp.where(own, values, 0).sum(axis=0)
            own_count[task] += own.sum(axis=0)
            common_sum[task] += xp.where(common, values, 0).sum(axis=0)
            common_count[task] += common.sum(axis=0)
            novel_sum[task] += xp.where(nov_valid, nov, 0).sum(axis=0)
            novel_count[task] += nov_valid.sum(axis=0)
            selected_ranks = rankings[task::2][batch_index, selected]
            for p in range(len(policies)):
                for seed in range(3):
                    ids = selected_ranks[:, p, seed]
                    mask = ids >= 0
                    rank = xp.broadcast_to(xp.arange(1, 21, dtype=xp.int32), ids.shape)
                    xp.minimum.at(first_rank[task, p, seed], ids[mask], rank[mask])
        cursor += len(batch)
        if cursor % (512 * 50) == 0:
            progress(f"Diversity replay: {cursor}/{len(users) * 2} requests")
    if cursor != len(users) * 2:
        raise ValueError("Incomplete ranking population")
    rows, shared = [], []
    for task, task_name in enumerate(("new", "repeat")):
        for p, policy in enumerate(policies):
            for j, k in enumerate(ks):
                for metric, totals, counts in (
                    ("ild", own_sum, own_count),
                    ("novelty", novel_sum, novel_count),
                    ("ild_common", common_sum, common_count),
                ):
                    n = counts[task, p, :, j]
                    value = (
                        float((totals[task, p, :, j] / xp.maximum(n, 1)).mean())
                        if bool((n > 0).all())
                        else None
                    )
                    row = dict(
                        condition=policy,
                        aspect="diversity",
                        task=task_name,
                        k=k,
                        metric=metric,
                        mean=value,
                        n_evaluated_min=int(n.min()),
                        n_evaluated_max=int(n.max()),
                        n_excluded_min=len(users) - int(n.max()),
                        n_excluded_max=len(users) - int(n.min()),
                    )
                    if metric == "ild_common":
                        shared.append(dict(row, metric="ild", n_evaluated=int(n[0])))
                    else:
                        rows.append(row)
                coverage = (first_rank[task, p] <= k).sum(axis=1) / len(items)
                rows.append(
                    dict(
                        condition=policy,
                        aspect="diversity",
                        task=task_name,
                        k=k,
                        metric="catalog_coverage",
                        mean=float(coverage.mean()),
                        n_requests=len(users),
                        catalog_items=len(items),
                    )
                )
    return rows, shared
