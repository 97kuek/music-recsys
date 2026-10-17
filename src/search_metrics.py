"""保存するランキングからK別指標を作る。推薦関数は呼ばない。"""

import math
from collections import defaultdict
from dataclasses import replace
from statistics import fmean

from src.evaluation.metrics import evaluate_response
from src.usage import summarize_usage


def quantile(values, q):
    if not values:
        return None
    values = sorted(values)
    x = (len(values) - 1) * q
    a = int(x)
    b = min(a + 1, len(values) - 1)
    return values[a] + (values[b] - values[a]) * (x - a)


def ranking_metrics(dataset, report, *, features=None):
    """Binaryと学習人気度によるNovelty。ILDは特徴が揃う要求だけ計算する。"""
    if dataset.sha256 != report.metadata.dataset_sha256:
        raise ValueError("比較条件不一致: dataset checksum")
    truths = {x.request_id: x for x in dataset.ground_truth}
    users = {}
    for event in dataset.train_interactions:
        users.setdefault(event.item_id, set()).add(event.user_id)
    n_users = len({x.user_id for x in dataset.train_interactions})
    universe = set().union(*(set(r.candidate_ids).difference(r.history) for r in dataset.requests))
    rows, details = [], []
    for k in range(1, 21):
        ndcg, recall, exposed = [], [], set()
        novelty, ild = defaultdict(list), defaultdict(list)
        evaluated_users = set()
        for run in report.runs:
            request = replace(run.request, top_k=k)
            evaluation = evaluate_response(request, run.response, truths[request.request_id])
            details.append(
                {
                    "request_id": request.request_id,
                    "user_id": request.user_id,
                    "history_count": len(request.history),
                    "k": k,
                    "ndcg": evaluation.ndcg,
                    "recall": evaluation.recall,
                }
            )
            if evaluation.ndcg is not None:
                evaluated_users.add(request.user_id)
                ndcg.append(evaluation.ndcg)
                recall.append(evaluation.recall)
            eligible = set(request.candidate_ids).difference(request.history)
            ranked = list(dict.fromkeys(x for x in run.response.item_ids if x in eligible))[:k]
            exposed.update(ranked)
            if ranked:
                novelty[request.user_id].append(
                    fmean(-math.log2((len(users.get(x, ())) + 1) / (n_users + 2)) for x in ranked)
                )
            if k >= 2 and len(ranked) >= 2 and features and all(features.get(x) for x in ranked):
                distances = []
                for j, a in enumerate(ranked):
                    for b in ranked[j + 1 :]:
                        va, vb = features[a], features[b]
                        na = math.sqrt(sum(v * v for v in va.values()))
                        nb = math.sqrt(sum(v * v for v in vb.values()))
                        if na and nb:
                            cosine = sum(v * vb.get(t, 0) for t, v in va.items()) / (na * nb)
                            distances.append(1 - min(1.0, max(0.0, cosine)))
                expected = len(ranked) * (len(ranked) - 1) // 2
                if len(distances) == expected:
                    ild[request.user_id].append(fmean(distances))
        rows.append(
            {
                "k": k,
                "ndcg": fmean(ndcg) if ndcg else None,
                "recall": fmean(recall) if recall else None,
                "graded_ndcg": None,
                "catalog_coverage": len(exposed) / len(universe) if universe else None,
                "novelty": fmean(fmean(v) for v in novelty.values()) if novelty else None,
                "ild": fmean(fmean(v) for v in ild.values()) if ild else None,
                "n_evaluated": len(ndcg),
                "n_evaluated_users": len(evaluated_users),
                "n_excluded": len(report.runs) - len(ndcg),
                "ild_evaluated": sum(len(v) for v in ild.values()),
                "ild_evaluated_users": len(ild),
            }
        )
    durations = [r.recommendation_seconds for r in report.runs]
    return {
        "metrics": rows,
        "requests": details,
        "efficiency": {
            "latency_p50": quantile(durations, 0.5),
            "latency_p95": quantile(durations, 0.95),
            "training_seconds": report.metadata.training_seconds,
            **(report.summary.usage or summarize_usage(r.response for r in report.runs))["metrics"],
        },
        "definitions": {
            "ranking": "binary-v1: filter invalid/duplicate IDs before truncation; generated once at K=20",
            "coverage": "union of recommendations / union of eligible candidate sets",
            "novelty": "user mean of -log2((distinct learning users for item+1)/(learning users+2))",
            "ild": "user mean pairwise cosine distance; full features and >=2 valid items required",
            "graded_ndcg": "未測定: 関連度定義は別条件として確定が必要",
            "unmeasured": "未測定はnull。usageに計測件数を保存。API CostはUSD、Peak Memoryは推薦要求内の最大bytes（学習は含まない）",
            "usage": report.summary.usage,
        },
    }
