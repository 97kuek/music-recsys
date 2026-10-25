"""Evaluate train-only neighbor prefixes on CUDA, sharing input reads and history masks."""

import copy
import time
from itertools import islice

import numpy as np

from src.infrastructure.datasets.full_io import DetailWriter, user_rows
from src.infrastructure.evaluation.gpu_evaluation import GPURanker
from src.infrastructure.evaluation.gpu_metrics import GPUAccuracy, GPUDiversity


def prefix_models(base, neighbor_counts):
    import cupy as cp
    from cupyx.scipy.sparse import csr_matrix as gpu_csr
    from scipy.sparse import csr_matrix

    histories = gpu_csr(base.histories)
    models = []
    for n in neighbor_counts:
        model = copy.copy(base)
        model.neighbors = n
        model.targets, model.weights = base.targets[:, :n], base.weights[:, :n]
        width = model.targets.shape[1]
        model.neighbor_matrix = csr_matrix(
            (model.weights.ravel(), model.targets.ravel(), np.arange(len(base.items) + 1) * width),
            shape=(len(base.items), len(base.items)),
            copy=True,
        )
        model.neighbor_matrix.eliminate_zeros()
        model._gpu = histories, gpu_csr(model.neighbor_matrix)
        models.append(model)
    cp.cuda.Stream.null.synchronize()
    return models


def score_prefixes(configs, models, paths, counts, features, item_users, progress):
    import cupy as cp

    rankers = [GPURanker(m) for m in models]
    if not rankers or any(not np.array_equal(r.order_cpu, rankers[0].order_cpu) for r in rankers):
        raise ValueError("Neighbor prefixes must share item/popularity order")
    accuracy = [GPUAccuracy(c.evaluation, m.index) for c, m in zip(configs, models, strict=True)]
    diversity = [
        GPUDiversity(features, m.items, item_users, counts["users"], c.evaluation.ks)
        for c, m in zip(configs, models, strict=True)
    ]
    details = [DetailWriter(c.output / "scores.parquet.partial") for c in configs]
    recommendations = [DetailWriter(c.output / "recommendations.parquet.partial") for c in configs]
    ranking_seconds, evaluation_seconds = np.zeros(len(models)), np.zeros(len(models))
    generation_calls, processed = 0, 0
    learning, observations = (
        iter(user_rows(paths["learning"])),
        iter(user_rows(paths["observations"])),
    )
    following = next(observations, None)
    try:
        while batch := list(islice(learning, 128)):
            users = [u for u, _ in batch]
            histories = [{r[1] for r in rows} for _, rows in batch]
            truths = []
            for user in users:
                if following is not None and following[0] < user:
                    raise ValueError("Observation outside learning population")
                rows = following[1] if following is not None and following[0] == user else []
                truths.append(rows)
                if rows:
                    following = next(observations, None)
            mask_started = time.perf_counter()
            ids = cp.asarray([models[0].users[u] for u in users])
            observed = models[0]._gpu[0][ids].toarray()[:, rankers[0].order].astype(cp.bool_)
            cp.cuda.Stream.null.synchronize()
            shared_mask_seconds = (time.perf_counter() - mask_started) / len(models)
            for i, ranker in enumerate(rankers):
                started = time.perf_counter()
                rankings, candidate_counts, elapsed = ranker.rank(
                    users, histories, observed=observed
                )
                ranking_seconds[i] += elapsed + shared_mask_seconds
                amortized = (elapsed + shared_mask_seconds) / (2 * len(users))
                for task in ("new", "repeat"):
                    ranking = rankings[task]
                    host = cp.asnumpy(ranking)
                    if i == 0:
                        generation_calls += int((candidate_counts[task] > 0).sum())
                    for j, user in enumerate(users):
                        recommendations[i].add(
                            dict(
                                user_id=user,
                                task=task,
                                item_ids=models[i].items[host[j][host[j] >= 0]].tolist(),
                                candidate_count=int(candidate_counts[task][j]),
                                latency_seconds=amortized,
                            )
                        )
                    for key, (values, valid) in accuracy[i].add(task, ranking, truths):
                        for j in np.flatnonzero(valid):
                            details[i].add(
                                dict(
                                    user_id=users[j],
                                    task=key[0],
                                    window_days=key[1],
                                    day_weight=float(key[2]) if key[2] is not None else -1.0,
                                    metric=key[3],
                                    values=values[j].tolist(),
                                )
                            )
                    diversity[i].add(task, ranking, candidate_counts[task])
                evaluation_seconds[i] += time.perf_counter() - started + shared_mask_seconds
            processed += len(users)
            if processed % 1024 == 0 or processed == counts["users"]:
                progress(
                    f"GPU prefixes {processed:,}/{counts['users']:,} users; {len(models)} neighbor counts"
                )
        if processed != counts["users"] or following is not None:
            raise ValueError("Population mismatch")
    finally:
        for writer in (*details, *recommendations):
            writer.close()
    results = []
    for i, model in enumerate(models):
        progress(f"GPU aggregating {configs[i].model}, n={model.neighbors}")
        started = time.perf_counter()
        rows = accuracy[i].finish(counts["users"])
        beyond = diversity[i].finish(
            dict(new=sum(n < counts["users"] for n in item_users.values()), repeat=len(model.items))
        )
        efficiency = dict(
            model=configs[i].model,
            latency_mean=ranking_seconds[i] / (2 * processed),
            generation_calls=generation_calls,
            training_seconds=model.training_seconds,
            ranking_seconds=float(ranking_seconds[i]),
            evaluation_seconds=float(evaluation_seconds[i] + time.perf_counter() - started),
            input_tokens=None,
            output_tokens=None,
            api_cost=None,
            llm_calls=None,
            tool_calls=None,
        )
        results.append((rows, beyond, efficiency))
    return results
