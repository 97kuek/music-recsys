"""モデル・Evaluatorで共有するnew / repeat候補の判定。"""

from music_recsys.types import RecommendationRequest


def eligible_candidates(request: RecommendationRequest) -> tuple[str, ...]:
    history = set(request.history)
    return tuple(
        item for item in request.candidate_ids if (item in history) == (request.task == "repeat")
    )
