"""単一要求の推薦実行と、履歴・候補・推薦根拠の分析用データ。"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from music_recsys.candidates import eligible_candidates
from music_recsys.data import BenchmarkDataset
from music_recsys.recommenders import create_recommender
from music_recsys.types import RecommendationRequest, RecommendationResponse


@dataclass(frozen=True)
class RecommendationInspection:
    model: str
    request: RecommendationRequest
    response: RecommendationResponse
    history: list[dict]
    recommendations: list[dict]
    candidates: list[dict]


def request_options(dataset: BenchmarkDataset) -> list[dict]:
    return [
        {
            "request_id": request.request_id,
            "user_id": request.user_id,
            "task": request.task,
            "history_count": len(request.history),
            "candidate_count": len(eligible_candidates(request)),
        }
        for request in dataset.requests
    ]


def inspect_recommendation(
    dataset: BenchmarkDataset,
    *,
    model: str,
    request_id: str,
    parameters: dict | None = None,
    seed: int = 0,
    features: dict | None = None,
) -> RecommendationInspection:
    """学習履歴だけでモデルを作り、選択した要求を実行する。正解は参照しない。"""
    request = next((r for r in dataset.requests if r.request_id == request_id), None)
    if request is None:
        raise ValueError(f"unknown request_id: {request_id}")
    from music_recsys.features import FEATURE_MODELS

    feature_args = {"features": features} if model in FEATURE_MODELS else {}
    recommender = create_recommender(
        model, dataset.train_interactions, parameters=parameters, seed=seed, **feature_args
    )
    response = recommender.recommend(request)
    counts = Counter(event.item_id for event in dataset.train_interactions)
    user_counts = Counter(
        event.item_id for event in dataset.train_interactions if event.user_id == request.user_id
    )
    ranks = {item_id: rank for rank, item_id in enumerate(response.item_ids, 1)}
    history = [
        {
            "item_id": item_id,
            "user_train_events": user_counts[item_id],
            "train_events": counts[item_id],
        }
        for item_id in request.history
    ]
    recommendations = [
        {
            "rank": ranks[item_id],
            "item_id": item_id,
            "train_events": counts[item_id],
            "in_history": item_id in request.history,
            "score": counts[item_id] if model == "popularity" else None,
            "reason": (
                f"学習全体で{counts[item_id]}回。{'既聴' if request.task == 'repeat' else '未聴'}候補を再生回数の降順、同数ならID昇順で選択。"
                if model == "popularity"
                else None
            ),
            **(recommender.explain(request, item_id) if hasattr(recommender, "explain") else {}),
        }
        for item_id in response.item_ids
    ]
    candidates = [
        {
            "item_id": item_id,
            "train_events": counts[item_id],
            "in_history": item_id in request.history,
            "recommended_rank": ranks.get(item_id),
        }
        for item_id in request.candidate_ids
    ]
    return RecommendationInspection(model, request, response, history, recommendations, candidates)


def audio_player(item_id: str, audio_files: Mapping[str, Path]):
    """指定されたローカル音源のみを、手動再生するプレイヤーにする。"""
    if item_id not in audio_files:
        return None
    path = Path(audio_files[item_id]).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"audio file not found for item_id: {item_id}")
    from IPython.display import Audio

    return Audio(filename=str(path), autoplay=False)


def feature_comparison(inspection, features):
    """登録済みの音楽特徴だけを比較する。欠損は0で補完しない。"""
    features = features or {}
    rows = []
    for group, items in (
        ("history", inspection.request.history),
        ("recommendation", inspection.response.item_ids),
    ):
        for item in items:
            vector = features.get(item)
            rows.append(
                {
                    "group": group,
                    "item_id": item,
                    "feature_status": "取得済み" if vector else "未取得",
                    **{f"feature:{k}": v for k, v in (vector or {}).items()},
                }
            )
    return rows
