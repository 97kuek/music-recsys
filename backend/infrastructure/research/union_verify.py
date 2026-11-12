"""Verify same-pool top-K equivalence and stream ranked stratified diagnostics."""

import hashlib
from collections import defaultdict
from itertools import islice

import numpy as np

from backend.infrastructure.datasets.candidates import candidate_batches
from backend.infrastructure.datasets.full_io import user_rows
from backend.infrastructure.research.union_strata import ranked_recall


def records(path):
    import pyarrow.parquet as pq

    for batch in pq.ParquetFile(path).iter_batches(batch_size=128):
        yield from batch.to_pylist()


def verify_source_order(row, plays, size):
    ids = row["item_indices"]
    if len(ids) != min(200, row["candidate_count"]) or any(i < 0 or i >= size for i in ids):
        raise ValueError("Candidate budget or catalog differs")
    expected = sorted(
        range(len(ids)), key=lambda i: (-row["raw_scores"][i], -plays[ids[i]], ids[i])
    )
    if expected != list(range(len(ids))):
        raise ValueError("EASE score/tie order differs")


def collect(
    root,
    seed,
    users,
    items,
    plays,
    history,
    popularity,
    observations,
    evaluation,
    engine,
    detail_writer,
    proof_writer,
    progress,
):
    xp = engine.xp
    host = np.asarray if xp is np else xp.asnumpy
    names = ["ease", f"bpr-s{seed}"]
    references = [dict(directory=root / "candidates" / name) for name in names]
    recs = [
        iter(records(root / name / "recommendations.parquet"))
        for name in ("ease", f"ease-bpr-rrf-s{seed}")
    ]
    observed = iter(user_rows(observations))
    current = next(observed, None)
    expected = [u for u, _ in users]
    index = {item: i for i, item in enumerate(items)}
    collected = defaultdict(lambda: [[], []])
    digest = hashlib.sha256()
    processed = 0
    for batch in candidate_batches(references):
        batch_users = [r[0]["user_id"] for r in batch[::2]]
        if batch_users != expected[processed : processed + len(batch_users)]:
            raise ValueError("Candidate users differ from full population")
        saved = [list(islice(stream, len(batch))) for stream in recs]
        if any(len(rows) != len(batch) for rows in saved):
            raise ValueError("Incomplete saved recommendations")
        truths = []
        for user in batch_users:
            if current is not None and current[0] < user:
                raise ValueError("Observation outside learning population")
            rows = current[1] if current is not None and current[0] == user else []
            truths.append(rows)
            if rows:
                current = next(observed, None)
        for pos, task in enumerate(("new", "repeat")):
            requests = batch[pos::2]
            arrays = []
            for source in range(2):
                a = np.full((len(batch_users), 200), -1, dtype=np.int32)
                for i, request in enumerate(requests):
                    row = request[source]
                    verify_source_order(row, plays, len(items))
                    a[i, : len(row["item_indices"])] = row["item_indices"]
                arrays.append(xp.asarray(a))
            pool, _, counts = engine.fuse(*arrays)
            rankings = [arrays[0][:, :20], pool[:, :20]]
            host_pool = host(pool)
            for user in batch_users:
                digest.update((user + "\0" + task + "\0").encode())
            digest.update(host_pool.tobytes())
            for condition, ranking in enumerate(rankings):
                for i, ids in enumerate(host(ranking)):
                    # Hybrid evaluator writes all new requests, then all repeat
                    # requests within each 64-user batch; candidates are user-major.
                    row = saved[condition][pos * len(batch_users) + i]
                    if (row["user_id"], row["task"]) != (batch_users[i], task) or row[
                        "item_ids"
                    ] != [items[j] for j in ids if j >= 0]:
                        raise ValueError("Saved recommendation differs from verified top-K")
                    if row["candidate_count"] != requests[i][0]["candidate_count"]:
                        raise ValueError("Recommendation eligible population differs")
                    if condition == 1 and row["pool_count"] != int(counts[i]):
                        raise ValueError("Saved RRF pool size differs")
            # A contains the scorer's first 100; all unseen candidates rank after them.
            # Therefore TopK(A union B, EASE) = A[:K] for K <= 100, with frozen ties.
            added = (rankings[1] >= 0) & ~(rankings[1][:, :, None] == arrays[0][:, None, :100]).any(
                axis=2
            )
            for i, user in enumerate(batch_users):
                proof_writer.add(
                    dict(
                        user_id=user,
                        task=task,
                        pool_count=int(counts[i]),
                        ease_item_ids=saved[0][pos * len(batch_users) + i]["item_ids"],
                        rrf_item_ids=saved[1][pos * len(batch_users) + i]["item_ids"],
                    )
                )
            for window in evaluation.window_days:
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
                for band, valid, values in ranked_recall(
                    rankings, truth, popularity, added, evaluation.ks, xp=xp
                ):
                    valid, values = host(valid), host(values)
                    if band is None:
                        strata = [("all", "all", np.ones(len(batch_users), dtype=bool))]
                        strata += [
                            (
                                "history",
                                f"Q{q + 1}",
                                history[processed : processed + len(batch_users)] == q,
                            )
                            for q in range(4)
                        ]
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
                            detail_writer.add(
                                dict(
                                    user_id=batch_users[i],
                                    task=task,
                                    window_days=window,
                                    stratum_kind=kind,
                                    stratum=label,
                                    ease=values[i, 0].tolist(),
                                    rrf=values[i, 1].tolist(),
                                    added=values[i, 2].tolist(),
                                )
                            )
        processed += len(batch_users)
        if processed % 8192 < 64:
            progress(f"Seed {seed}: {processed:,}/{len(users):,} users")
    if (
        processed != len(users)
        or current is not None
        or any(next(s, None) is not None for s in recs)
    ):
        raise ValueError("Incomplete candidate/observation/recommendation population")
    return {
        key: (ids, np.concatenate(parts)) for key, (ids, parts) in collected.items()
    }, digest.hexdigest()
