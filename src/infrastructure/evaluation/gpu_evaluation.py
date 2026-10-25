"""CUDA batching for ranking, listening metrics, diversity and bootstrap intervals."""

import time
from itertools import islice

import numpy as np

from src.infrastructure.evaluation.gpu_metrics import GPUAccuracy, GPUDiversity


class GPURanker:
    def __init__(self, model):
        import cupy as cp

        self.cp, self.model = cp, model
        self.popularity = hasattr(model, "scores")
        self.order_cpu = np.lexsort(
            (np.arange(len(model.items)), -model.scores if self.popularity else -model.plays)
        )
        self.inverse = np.empty(len(model.items), dtype=np.int32)
        self.inverse[self.order_cpu] = np.arange(len(model.items))
        self.order = cp.asarray(self.order_cpu)
        if self.popularity:
            self.scores = cp.asarray(model.scores[self.order_cpu])
        elif not hasattr(model, "gpu_score_batch"):
            self.users = cp.asarray(model.user_factors)
            self.items = cp.asarray(model.item_factors[self.order_cpu])

    def candidates(self, users, histories, *, top_m=100, observed=None):
        if type(top_m) is not int or top_m < 1:
            raise ValueError("top_m must be a positive integer")
        cp, model = self.cp, self.model
        started = time.perf_counter()
        if self.popularity:
            scores = cp.broadcast_to(self.scores, (len(users), len(model.items)))
        else:
            user_ids = np.asarray([model.users[u] for u in users])
            scores = (
                model.gpu_score_batch(user_ids)[:, self.order]
                if hasattr(model, "gpu_score_batch")
                else self.users[cp.asarray(user_ids)] @ self.items.T
            )
        if hasattr(model, "active_users"):
            scores[cp.asarray(~model.active_users[user_ids])] = 0
        if observed is None:
            observed = cp.zeros(scores.shape, dtype=bool)
            rows, columns = [], []
            for row, history in enumerate(histories):
                rows.extend([row] * len(history))
                columns.extend(self.inverse[model.index[i]] for i in history)
            if rows:
                observed[cp.asarray(rows), cp.asarray(columns)] = True
        elif observed.shape != scores.shape or observed.dtype != cp.bool_:
            raise ValueError("Shared history mask must match the ranked item order")
        rankings, counts, raw_scores = {}, {}, {}
        for task in ("new", "repeat"):
            allowed = ~observed if task == "new" else observed
            masked = cp.where(allowed, scores, -cp.inf)
            # CuPy argsort is stable: prior popularity/ID order resolves score ties.
            selected = cp.argsort(-masked, axis=1)[:, : min(top_m, len(model.items))]
            count = np.asarray(
                [len(h) if task == "repeat" else len(model.items) - len(h) for h in histories]
            )
            result = cp.full((len(users), top_m), -1, dtype=cp.int32)
            ranked = self.order[selected]
            result[:, : ranked.shape[1]] = cp.where(
                cp.arange(ranked.shape[1])[None, :] < cp.asarray(count)[:, None], ranked, -1
            )
            raw = cp.full((len(users), top_m), cp.nan, dtype=scores.dtype)
            raw[:, : ranked.shape[1]] = cp.where(
                cp.arange(ranked.shape[1])[None, :] < cp.asarray(count)[:, None],
                cp.take_along_axis(scores, selected, axis=1),
                cp.nan,
            )
            rankings[task], counts[task], raw_scores[task] = result, count, raw
        cp.cuda.Stream.null.synchronize()
        return rankings, counts, raw_scores, time.perf_counter() - started

    def rank(self, users, histories, *, observed=None):
        rankings, counts, _, elapsed = self.candidates(
            users, histories, top_m=20, observed=observed
        )
        return rankings, counts, elapsed


def score_gpu(
    config, paths, counts, features, item_users, popularity, rank, factor_model, itemcf, progress
):
    import cupy as cp

    from src.infrastructure.datasets.full_io import DetailWriter, user_rows

    started = time.perf_counter()
    factor_model = factor_model if factor_model is not None else itemcf
    if factor_model is None:
        raise ValueError("GPU scoring requires a compatible trained model")
    ranker = GPURanker(factor_model)
    accuracy = GPUAccuracy(config.evaluation, factor_model.index)
    diversity = GPUDiversity(
        features, factor_model.items, item_users, counts["users"], config.evaluation.ks
    )
    details = DetailWriter(config.output / "scores.parquet.partial")
    recommendations = DetailWriter(config.output / "recommendations.parquet.partial")
    learning = iter(user_rows(paths["learning"]))
    observations = iter(user_rows(paths["observations"]))
    next_observation = next(observations, None)
    processed, generation_calls, ranking_seconds = 0, 0, 0.0
    progress(f"GPU evaluating {counts['users']:,} users in batches of 128")
    try:
        while batch := list(islice(learning, 128)):
            users = [u for u, _ in batch]
            histories = [{r[1] for r in rows} for _, rows in batch]
            truths = []
            for user in users:
                if next_observation is not None and next_observation[0] < user:
                    raise ValueError("observations contain user outside learning population")
                rows = (
                    next_observation[1]
                    if next_observation is not None and next_observation[0] == user
                    else []
                )
                truths.append(rows)
                if rows:
                    next_observation = next(observations, None)
            rankings, candidate_counts, elapsed = ranker.rank(users, histories)
            ranking_seconds += elapsed
            amortized = elapsed / (2 * len(users))
            for task in ("new", "repeat"):
                ranking = rankings[task]
                host_ranking = cp.asnumpy(ranking)
                generation_calls += int((candidate_counts[task] > 0).sum())
                for index, user in enumerate(users):
                    indices = host_ranking[index]
                    recommendations.add(
                        dict(
                            user_id=user,
                            task=task,
                            item_ids=factor_model.items[indices[indices >= 0]].tolist(),
                            candidate_count=int(candidate_counts[task][index]),
                            latency_seconds=amortized,
                        )
                    )
                for key, (values, valid) in accuracy.add(task, ranking, truths):
                    for index in np.flatnonzero(valid):
                        details.add(
                            dict(
                                user_id=users[index],
                                task=key[0],
                                window_days=key[1],
                                day_weight=float(key[2]) if key[2] is not None else -1.0,
                                metric=key[3],
                                values=values[index].tolist(),
                            )
                        )
                diversity.add(task, ranking, candidate_counts[task])
            processed += len(users)
            if processed % 1024 == 0 or processed == counts["users"]:
                progress(f"GPU evaluated {processed:,}/{counts['users']:,} users")
        if processed != counts["users"] or next_observation is not None:
            raise ValueError("population mismatch")
    finally:
        details.close()
        recommendations.close()
    progress("GPU aggregating scores and bootstrap intervals")
    rows = accuracy.finish(counts["users"])
    beyond = diversity.finish(
        {"new": sum(n < counts["users"] for n in item_users.values()), "repeat": len(popularity)}
    )
    efficiency = dict(
        model=config.model,
        latency_mean=ranking_seconds / (2 * processed) if processed else None,
        generation_calls=generation_calls,
        training_seconds=factor_model.training_seconds,
        evaluation_seconds=time.perf_counter() - started,
        ranking_seconds=ranking_seconds,
        input_tokens=None,
        output_tokens=None,
        api_cost=None,
        llm_calls=None,
        tool_calls=None,
    )
    return rows, beyond, efficiency
