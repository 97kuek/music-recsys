"""Read-only common-user ILD on aligned saved rankings, with source-value verification."""

import numpy as np

from src.infrastructure.evaluation.features import FeaturePrefixes
from src.infrastructure.research.complementarity import recommendation_batches


def common_ild(sources, features, items, ks, *, xp=np, batch_users=128):
    prefixes = FeaturePrefixes(features, items, xp)
    index = {item: i for i, item in enumerate(items)}
    totals = xp.zeros((2, 2, len(sources), len(ks)), dtype=xp.float64)
    counts = xp.zeros(totals.shape, dtype=xp.int64)
    positions = xp.asarray(ks) - 1
    users = 0
    for batch in recommendation_batches(sources, batch_users):
        arrays = np.full((len(batch), len(sources), 20), -1, dtype=np.int32)
        for i, request in enumerate(batch):
            for j, row in enumerate(request):
                arrays[i, j, : len(row["item_ids"])] = [index[x] for x in row["item_ids"]]
        values, valid = prefixes.calculate(xp.asarray(arrays))
        values, valid = values[:, :, positions], valid[:, :, positions]
        for t in range(2):
            individual = valid[t::2]
            for s, mask in enumerate(
                (individual, xp.broadcast_to(individual.all(axis=1)[:, None, :], individual.shape))
            ):
                totals[t, s] += xp.where(mask, values[t::2], 0).sum(axis=0)
                counts[t, s] += mask.sum(axis=0)
        users += len(batch) // 2
    host = (lambda x: x) if xp is np else xp.asnumpy
    totals, counts = host(totals), host(counts)
    rows = []
    for j, (name, source) in enumerate(sources.items()):
        if users != source["result"]["population"]["users"]:
            raise ValueError("Common ILD population differs")
        for t, task in enumerate(("new", "repeat")):
            for p, k in enumerate(ks):
                for s, scope in enumerate(("individual", "common")):
                    n = int(counts[t, s, j, p])
                    mean = float(totals[t, s, j, p] / n) if n else None
                    if s == 0:
                        previous = next(
                            r
                            for r in source["result"]["diversity"]
                            if r["task"] == task and r["k"] == k
                        )
                        if (
                            n != previous["ild_evaluated"]
                            or ((mean is None) != (previous["ild"] is None))
                            or (
                                mean is not None
                                and not np.isclose(mean, previous["ild"], atol=1e-12, rtol=1e-12)
                            )
                        ):
                            raise ValueError(
                                "Corrected ILD does not reproduce its saved value/population"
                            )
                    rows.append(
                        dict(
                            condition=name,
                            task=task,
                            k=k,
                            scope=scope,
                            mean=mean,
                            n_evaluated=n,
                            n_excluded=users - n,
                        )
                    )
    return rows
