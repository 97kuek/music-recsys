"""全件評価用の先頭20曲の採点 / listening-v2と同じ定義を累積和で計算する。"""

import math
from collections import Counter, defaultdict
from functools import lru_cache

import numpy as np

from music_recsys.features import unit_vector


def accuracy_vectors(ranking, observations, *, task, config):
    """候補内の観測を受け取り、条件ごとのK配列を返す。正解なしはNone。"""
    discounts = 1 / np.log2(np.arange(2, 22))
    positions = np.asarray(config.ks) - 1
    for window in config.window_days:
        truth = {i: (n, d) for i, w, n, d in observations if w == window}
        binary = np.array([float(i in truth) for i in ranking] + [0.0] * (20 - len(ranking)))
        ideal_binary = np.cumsum(discounts * (np.arange(20) < len(truth)))
        dcg = np.cumsum(binary * discounts)
        name = "discovery" if task == "new" else "repeat"
        if task == "new":
            yield (
                (name, window, None, "binary_ndcg"),
                (dcg / ideal_binary)[positions] if truth else None,
            )
        yield (
            (name, window, None, "recall" if task == "new" else "repeat_recall"),
            np.cumsum(binary)[positions] / len(truth) if truth else None,
        )
        for weight in config.day_weights:
            gains = {i: math.log2(1 + n) + weight * math.log2(1 + d) for i, (n, d) in truth.items()}
            ranked = np.array([gains.get(i, 0.0) for i in ranking] + [0.0] * (20 - len(ranking)))
            ideal = sorted(gains.values(), reverse=True)[:20]
            ideal += [0.0] * (20 - len(ideal))
            denom = np.cumsum(np.array(ideal) * discounts)
            yield (
                ("retention_proxy" if task == "new" else "repeat", window, weight, "graded_ndcg"),
                (np.cumsum(ranked * discounts) / denom)[positions] if truth else None,
            )


class DiversityAccumulator:
    def __init__(self, features, item_users, n_users, ks):
        self.features = features
        self.vectors = {i: unit_vector(v) for i, v in features.items()}
        self.novelty = {i: -math.log2((n + 1) / (n_users + 2)) for i, n in item_users.items()}
        self.ks = ks
        self.groups = defaultdict(
            lambda: {
                "count": 0,
                "ild_sum": 0.0,
                "ild_count": 0,
                "novelty_sum": 0.0,
                "novelty_count": 0,
                "exposed": set(),
                "reasons": Counter(),
            }
        )
        # Bound memory even when many different tracks are recommended
        self.distance = lru_cache(maxsize=100_000)(self._distance)

    def _distance(self, a, b):
        cosine = math.fsum(v * self.vectors[b].get(t, 0) for t, v in self.vectors[a].items())
        return 1 - min(1.0, max(0.0, cosine))

    def add(self, task, ranking, candidate_count):
        distances = [0.0]
        valid = True
        total = 0.0
        for j, item in enumerate(ranking):
            valid = valid and bool(self.vectors.get(item))
            if valid:
                total += sum(self.distance(*sorted((item, prev))) for prev in ranking[:j])
            distances.append(total if valid else None)
        novelty = np.cumsum([self.novelty[i] for i in ranking])
        for k in self.ks:
            row = self.groups[task, k]
            row["count"] += 1
            ranked = ranking[:k]
            row["exposed"].update(ranked)
            n = len(ranked)
            if n:
                row["novelty_sum"] += float(novelty[n - 1]) / n
                row["novelty_count"] += 1
            if n >= 2 and distances[n] is not None:
                row["ild_sum"] += distances[n] / (n * (n - 1) / 2)
                row["ild_count"] += 1
            else:
                reasons = (
                    ["empty_candidates"]
                    if not candidate_count
                    else ["insufficient_valid_items"]
                    if n < 2
                    else []
                )
                if n >= 2:
                    if any(i not in self.features for i in ranked):
                        reasons.append("features_missing")
                    if any(i in self.features and not self.vectors[i] for i in ranked):
                        reasons.append("features_zero_norm")
                row["reasons"].update(reasons)

    def finish(self, universe_sizes):
        result = []
        for (task, k), g in sorted(self.groups.items()):
            size = universe_sizes[task]
            row = dict(
                task=task,
                k=k,
                candidate_items=size,
                exposed_items=len(g["exposed"]),
                catalog_coverage=len(g["exposed"]) / size if size else None,
                n_requests=g["count"],
                ild_exclusion_counts=dict(g["reasons"]),
            )
            for metric in ("ild", "novelty"):
                n = g[f"{metric}_count"]
                row[metric] = g[f"{metric}_sum"] / n if n else None
                row[f"{metric}_evaluated"] = n
                row[f"{metric}_excluded"] = g["count"] - n
            result.append(row)
        return result
