"""非負特徴の検証と正規化。"""

import math

from src.domain.recommendation.contracts import validate_id

FeatureMap = dict[str, dict[str, float]]
FEATURE_MODELS = frozenset({"content", "hybrid"})


def validate_features(features: object) -> FeatureMap:
    if not isinstance(features, dict):
        raise ValueError("features must be an item / vector mapping")
    for item, vector in features.items():
        validate_id(item)
        if not isinstance(vector, dict):
            raise ValueError("特徴は非負の有限値の辞書が必要です")
        for name, value in vector.items():
            validate_id(name)
            try:
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("特徴は非負の有限値の辞書が必要です")
    return features


def unit_vector(vector: dict) -> dict:
    """先に最大値で縮尺を揃え、有限の大きな非負特徴でもL2正規化する。"""
    scale = max(vector.values(), default=0)
    if not scale:
        return {}
    scaled = {k: v / scale for k, v in sorted(vector.items()) if v > 0}
    norm = math.hypot(*scaled.values())
    return {k: v / norm for k, v in scaled.items()}
