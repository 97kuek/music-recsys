"""v2の保存された先頭位置だけから、多様性・発見性をタスク別に測る。"""

import math
from collections import defaultdict
from statistics import fmean

from music_recsys.candidates import eligible_candidates
from music_recsys.features import unit_vector, validate_features


def listening_diversity(dataset, runs, *, ks, features=None) -> dict:
    if any(type(k) is not int or not 1 <= k <= 20 for k in ks) or len(set(ks)) != len(ks):
        raise ValueError("K must be unique integers in 1..20")
    features = validate_features(features) if features is not None else {}
    item_users = defaultdict(set)
    learning_users = set()
    for event in dataset.train_interactions:
        item_users[event.item_id].add(event.user_id)
        learning_users.add(event.user_id)
    n_users = len(learning_users)
    universe = defaultdict(set)
    requests = {r.request_id: r for r in dataset.requests}
    for request in dataset.requests:
        universe[request.task].update(eligible_candidates(request))
    if len(runs) != len(requests) or {r["request"]["request_id"] for r in runs} != set(requests):
        raise ValueError("diversity requests must match dataset")
    vectors = {item: unit_vector(vector) for item, vector in features.items()}
    metrics, details = [], []
    grouped = defaultdict(list)
    for run in runs:
        request = requests[run["request"]["request_id"]]
        response = run["response"]
        eligible = set(eligible_candidates(request))
        failed = response["status"] == "error"
        for k in ks:
            seen, ranked = set(), []
            if not failed:
                for item in response["item_ids"][:k]:
                    if item in eligible and item not in seen:
                        seen.add(item)
                        ranked.append(item)
            novelty = (
                fmean(-math.log2((len(item_users[x]) + 1) / (n_users + 2)) for x in ranked)
                if ranked
                else None
            )
            ild = None
            if k >= 2 and len(ranked) >= 2 and all(vectors.get(x) for x in ranked):
                distances = []
                for i, a in enumerate(ranked):
                    for b in ranked[i + 1 :]:
                        # Normalize before products to avoid overflow from large finite features.
                        cosine = math.fsum(v * vectors[b].get(t, 0) for t, v in vectors[a].items())
                        distances.append(1 - min(1.0, max(0.0, cosine)))
                ild = fmean(distances)
            row = {
                "request_id": request.request_id,
                "user_id": request.user_id,
                "task": request.task,
                "k": k,
                "ild": ild,
                "novelty": novelty,
                "valid_items": len(ranked),
                "failed": failed,
            }
            details.append(row)
            grouped[(request.task, k)].append((row, ranked))
    for (task, k), values in sorted(grouped.items()):
        exposed = {item for _, ranked in values for item in ranked}
        row = {
            "task": task,
            "k": k,
            "catalog_coverage": len(exposed) / len(universe[task]) if universe[task] else None,
            "n_requests": len(values),
            "n_failed": sum(r["failed"] for r, _ in values),
            "candidate_items": len(universe[task]),
            "exposed_items": len(exposed),
        }
        for name in ("ild", "novelty"):
            users = defaultdict(list)
            for r, _ in values:
                if r[name] is not None:
                    users[r["user_id"]].append(r[name])
            row[name] = fmean(fmean(xs) for xs in users.values()) if users else None
            row[f"{name}_evaluated"] = len(users)
            row[f"{name}_excluded"] = len(values) - len(users)
        metrics.append(row)
    return {
        "metrics": metrics,
        "requests": details,
        "definitions": {
            "ranking": "listening-v2: first K positions; invalid/duplicate holes retained; failed output empty",
            "population": "all learning users; independent of future positives; new/repeat separate",
            "coverage": "unique valid exposed items / union of task eligible candidates; empty denominator null",
            "novelty": "user mean -log2((distinct learning users for item+1)/(learning users+2)); no valid output null",
            "ild": "user mean pairwise cosine distance; K>=2 and all valid items have nonzero features; otherwise null",
            "window_gain": "not applicable: one measurement per task/K, no duplication by evaluation window or day weight",
        },
    }
