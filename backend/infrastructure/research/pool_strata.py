"""Training-only bands and GPU candidate-recall diagnostics, without model execution."""

from collections import defaultdict

import numpy as np

from backend.infrastructure.datasets.candidates import candidate_batches
from backend.infrastructure.datasets.full_io import user_rows
from backend.infrastructure.research.complementarity import tie_safe_quartiles

SCOPES = ("source_a100", "source_b100", "union100", "source_a200", "source_b200")


def training_bands(learning):
    import duckdb

    with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as con:
        con.read_parquet(str(learning)).create_view("learning")
        users = con.execute(
            "SELECT user_id,count(*) FROM learning GROUP BY user_id ORDER BY user_id"
        ).fetchall()
        items = con.execute(
            "SELECT item_id,count(*),sum(play_count)::DOUBLE FROM learning GROUP BY item_id ORDER BY item_id"
        ).fetchall()
    history, hm = tie_safe_quartiles([r[1] for r in users])
    popularity, pm = tie_safe_quartiles([r[1] for r in items])
    return (
        users,
        items,
        history,
        popularity,
        dict(
            history=dict(definition="distinct learning tracks per user", **hm),
            popularity=dict(definition="distinct learning users per track", **pm),
        ),
    )


def recall_values(sources, truths, bands, *, xp):
    """Count each truth once, including overlap in the union; -1 is padding only."""
    width = max(1, max(map(len, truths), default=0))
    ids = np.full((len(truths), width), -1, dtype=np.int32)
    for i, row in enumerate(truths):
        ids[i, : len(row)] = row
    ids = xp.asarray(ids)
    band_ids = xp.asarray(bands)[xp.maximum(ids, 0)]
    hits = [
        xp.any(a[:, :, None] == ids[:, None, :], axis=1)
        for a in (sources[0][:, :100], sources[1][:, :100], sources[0], sources[1])
    ]
    hits.insert(2, hits[0] | hits[1])
    for band in (None, 0, 1, 2, 3):
        valid_truth = ids >= 0
        if band is not None:
            valid_truth &= band_ids == band
        counts = valid_truth.sum(axis=1)
        values = xp.stack(
            [(hit & valid_truth).sum(axis=1) / xp.maximum(counts, 1) for hit in hits], axis=1
        )
        yield band, counts > 0, values


def collect(references, observations, users, items, history, popularity, windows, *, xp, writer):
    """Stream every learning user, returning eligible user-aligned values per band."""
    index = {row[0]: i for i, row in enumerate(items)}
    expected = [row[0] for row in users]
    observed = iter(user_rows(observations))
    current = next(observed, None)
    collected = defaultdict(lambda: [[], []])
    processed = 0
    host = np.asarray if xp is np else xp.asnumpy
    for batch in candidate_batches(references):
        batch_users = [r[0]["user_id"] for r in batch[::2]]
        if batch_users != expected[processed : processed + len(batch_users)]:
            raise ValueError("Candidate users differ from the full learning population")
        truths = []
        for user in batch_users:
            if current is not None and current[0] < user:
                raise ValueError("Observation outside learning population")
            rows = current[1] if current is not None and current[0] == user else []
            truths.append(rows)
            if rows:
                current = next(observed, None)
        history_batch = history[processed : processed + len(batch_users)]
        for position, task in enumerate(("new", "repeat")):
            arrays = []
            for source in range(2):
                array = np.full((len(batch_users), 200), -1, dtype=np.int32)
                for i, request in enumerate(batch[position::2]):
                    row = request[source]
                    ids = row["item_indices"]
                    if len(ids) != min(200, row["candidate_count"]) or any(
                        v < 0 or v >= len(items) for v in ids
                    ):
                        raise ValueError("Candidate retrieval budget or catalog index differs")
                    array[i, : len(ids)] = ids
                arrays.append(xp.asarray(array))
            for window in windows:
                truth = [
                    sorted(
                        {
                            index[r[1]]
                            for r in rows
                            if r[2] == window and (r[3] > 0) == (task == "repeat")
                        }
                    )
                    for rows in truths
                ]
                for band, valid, values in recall_values(arrays, truth, popularity, xp=xp):
                    valid = host(valid)
                    values = host(values)
                    strata = [("all", "all", np.ones(len(batch_users), dtype=bool))]
                    if band is None:
                        strata += [("history", f"Q{q + 1}", history_batch == q) for q in range(4)]
                    else:
                        strata = [
                            ("popularity", f"Q{band + 1}", np.ones(len(batch_users), dtype=bool))
                        ]
                    for kind, label, mask in strata:
                        key = task, window, kind, label
                        selected = np.flatnonzero(valid & mask)
                        bucket = collected[key]
                        bucket[0].extend(batch_users[i] for i in selected)
                        bucket[1].append(values[selected])
                        for i in selected:
                            writer.add(
                                dict(
                                    user_id=batch_users[i],
                                    task=task,
                                    window_days=window,
                                    stratum_kind=kind,
                                    stratum=label,
                                    values=values[i].tolist(),
                                )
                            )
        processed += len(batch_users)
    if processed != len(users) or current is not None:
        raise ValueError("Candidate/observation population is incomplete")
    return {key: (ids, np.concatenate(parts)) for key, (ids, parts) in collected.items()}


def summarize(seeds, history, *, group, xp):
    """Average seeds per user before paired differences; empty bands remain null."""
    rows = []
    if any(set(seed) != set(seeds[0]) for seed in seeds):
        raise ValueError("Seed strata differ")
    for key, (users, _) in seeds[0].items():
        task, window, kind, band = key
        if any(seed[key][0] != users for seed in seeds):
            raise ValueError("Seed eligible users differ")
        n = len(users)
        population = (
            int((history == int(band[1:]) - 1).sum()) if kind == "history" else len(history)
        )
        values = xp.stack([xp.asarray(seed[key][1]) for seed in seeds]).mean(axis=0)
        means = values.mean(axis=0) if n else None
        definitions = [(label, i, False) for i, label in enumerate(SCOPES)]
        definitions += [
            ("union_minus_" + label, i, True) for i, label in enumerate(SCOPES) if i != 2
        ]
        for label, i, difference in definitions:
            value = (
                float((values[:, 2] - values[:, i]).mean() if difference else means[i])
                if n
                else None
            )
            rows.append(
                dict(
                    group=group,
                    task=task,
                    window_days=window,
                    stratum_kind=kind,
                    stratum=band,
                    scope=label,
                    metric="recall_difference" if difference else "candidate_recall",
                    mean=value,
                    n_population=population,
                    n_evaluated=n,
                    n_excluded=population - n,
                    seeds=len(seeds),
                )
            )
    return rows
