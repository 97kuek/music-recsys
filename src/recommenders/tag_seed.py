"""明示選択した楽曲のタグ特徴を使うContent-based推薦。"""

import math
from collections import Counter

from src.candidates import eligible_candidates
from src.features import unit_vector, validate_features
from src.types import RecommendationResponse


class TagSeedRecommender:
    """履歴を捏造しないタグCosine。request.historyは明示選択seed。"""

    def __init__(self, features):
        validate_features(features)
        self.index = tuple(features)
        self.als_fold_in = False
        self.vectors = {item: unit_vector(vector) for item, vector in features.items()}

    def recommend(self, request):
        profile = Counter()
        for item in request.history:
            profile.update(self.vectors.get(item, {}))
        profile = unit_vector(dict(profile))
        scored = []
        for item in eligible_candidates(request):
            score = math.fsum(v * profile.get(t, 0) for t, v in self.vectors.get(item, {}).items())
            if score > 0:
                scored.append((-score, item))
        return RecommendationResponse(
            request.request_id, tuple(x[1] for x in sorted(scored)[: request.top_k])
        )
