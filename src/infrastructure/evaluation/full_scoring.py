"""CPU reference evaluator for full listening-v2 experiments."""

import time
from collections import defaultdict
from itertools import islice

import numpy as np

from src.infrastructure.datasets.full_io import DetailWriter, user_rows
from src.infrastructure.evaluation.streaming_metrics import DiversityAccumulator, accuracy_vectors


def score_cpu(
    config, paths, counts, features, item_users, popularity, rank, factor_model, itemcf, progress
):
    output = config.output
    training_seconds = factor_model.training_seconds if factor_model is not None else 0
    evaluation_started = time.perf_counter()
    accuracy = defaultdict(
        lambda: {"sum": np.zeros(len(config.evaluation.ks)), "n": 0, "primary": []}
    )
    diversity = DiversityAccumulator(features, item_users, counts["users"], config.evaluation.ks)
    details = DetailWriter(output / "scores.parquet.partial")
    recommendations = DetailWriter(output / "recommendations.parquet.partial")
    observation_iter = iter(user_rows(paths["observations"]))
    next_observation = next(observation_iter, None)
    durations = []
    processed = 0
    progress(
        f"Evaluating {counts['users']:,} users / {counts['items']:,} tracks / {counts['events']:,} plays"
    )
    try:
        for user, history_rows in user_rows(paths["learning"]):
            history = {row[1] for row in history_rows}
            if next_observation is not None and next_observation[0] < user:
                raise ValueError("observations contain user outside learning population")
            observations = (
                next_observation[1]
                if next_observation is not None and next_observation[0] == user
                else []
            )
            if observations:
                next_observation = next(observation_iter, None)
            for task in ("new", "repeat"):
                candidate_count = (
                    len(history) if task == "repeat" else len(popularity) - len(history)
                )
                t = time.perf_counter()
                ranking = (
                    factor_model.rank(user, history, task)
                    if factor_model is not None
                    else itemcf.rank(history, task)
                    if itemcf is not None
                    else (
                        sorted(history, key=rank.__getitem__)[:20]
                        if task == "repeat"
                        else list(islice((i for i in popularity if i not in history), 20))
                    )
                )
                elapsed = time.perf_counter() - t
                if candidate_count:
                    durations.append(elapsed)
                recommendations.add(
                    dict(
                        user_id=user,
                        task=task,
                        item_ids=ranking,
                        candidate_count=candidate_count,
                        latency_seconds=elapsed,
                    )
                )
                truth = [
                    (r[1], r[2], r[5], r[6])
                    for r in observations
                    if (r[3] > 0) == (task == "repeat")
                ]
                for key, values in accuracy_vectors(
                    ranking, truth, task=task, config=config.evaluation
                ):
                    a = accuracy[key]
                    if values is not None:
                        a["sum"] += values
                        a["n"] += 1
                        if key[1] == config.evaluation.primary_window_days and key[2] in (
                            None,
                            config.evaluation.primary_day_weight,
                        ):
                            a["primary"].append(values)
                        details.add(
                            dict(
                                user_id=user,
                                task=key[0],
                                window_days=key[1],
                                day_weight=float(key[2]) if key[2] is not None else -1.0,
                                metric=key[3],
                                values=values.tolist(),
                            )
                        )
                diversity.add(task, ranking, candidate_count)
            processed += 1
            if processed % 1000 == 0:
                progress(f"Evaluated {processed:,}/{counts['users']:,} users")
        if processed != counts["users"] or next_observation is not None:
            raise ValueError("population mismatch")
    finally:
        details.close()
        recommendations.close()
    progress("Aggregating scores and user bootstrap intervals")
    rows = []
    for key, a in accuracy.items():
        ci = None
        if a["primary"]:
            values = np.asarray(a["primary"])
            rng = np.random.default_rng(42)
            means = np.empty((2000, len(config.evaluation.ks)))
            for b in range(len(means)):
                means[b] = values[rng.integers(0, len(values), len(values))].mean(axis=0)
            ci = np.quantile(means, [0.025, 0.975], axis=0)
        for index, k in enumerate(config.evaluation.ks):
            rows.append(
                dict(
                    task=key[0],
                    window_days=key[1],
                    day_weight=key[2],
                    metric=key[3],
                    k=k,
                    mean=float(a["sum"][index] / a["n"]) if a["n"] else None,
                    n_evaluated=a["n"],
                    n_excluded=counts["users"] - a["n"],
                    ci_low=float(ci[0, index]) if ci is not None else None,
                    ci_high=float(ci[1, index]) if ci is not None else None,
                )
            )
    beyond = diversity.finish(
        {
            "new": sum(n < counts["users"] for n in item_users.values()),
            "repeat": len(popularity),
        }
    )
    efficiency = dict(
        model=config.model,
        latency_mean=sum(durations) / len(durations) if durations else None,
        generation_calls=len(durations),
        training_seconds=training_seconds,
        evaluation_seconds=time.perf_counter() - evaluation_started,
        input_tokens=None,
        output_tokens=None,
        api_cost=None,
        llm_calls=None,
        tool_calls=None,
    )
    return rows, beyond, efficiency
