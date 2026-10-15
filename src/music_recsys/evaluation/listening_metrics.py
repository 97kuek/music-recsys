"""観測回数・日数のgainと、順位を詰めないlistening-v2の指標。"""

import math

from music_recsys.candidates import eligible_candidates
from music_recsys.listening_types import (
    ListeningScore,
    positive_integer,
)
from music_recsys.listening_types import (
    day_weight as validate_weight,
)


def graded_gain(play_count, active_days, day_weight):
    validate_weight(day_weight)
    if (
        type(play_count) is not int
        or type(active_days) is not int
        or not 0 <= active_days <= play_count
        or (play_count == 0) != (active_days == 0)
    ):
        raise ValueError("invalid playback counts")
    return math.log2(1 + play_count) + day_weight * math.log2(1 + active_days)


def evaluate_listening_response(request, response, observations, *, window_days, k, day_weight):
    positive_integer(window_days, "window_days")
    positive_integer(k, "K")
    validate_weight(day_weight)
    if k > 20 or request.top_k != 20:
        raise ValueError("listening-v2 uses one top-20 ranking and K=1..20")
    if request.request_id != response.request_id:
        raise ValueError("request_id mismatch")
    eligible, history = set(eligible_candidates(request)), set(request.history)
    gains = {}
    seen_observations = set()
    for row in observations:
        if row.user_id != request.user_id or (row.learning_play_count > 0) != (
            row.item_id in history
        ):
            raise ValueError("observation user / learning history mismatch")
        key = (row.item_id, row.window_days)
        if key in seen_observations:
            raise ValueError("duplicate observation")
        seen_observations.add(key)
        if row.window_days == window_days and row.item_id in eligible:
            gains[row.item_id] = graded_gain(
                row.evaluation_play_count, row.evaluation_active_days, day_weight
            )
    prefix = response.item_ids[:20][:k]
    seen, valid, invalid, duplicate, binary_dcg, graded_dcg, hits = set(), 0, 0, 0, 0.0, 0.0, 0
    for rank, item in enumerate(prefix, 1):
        if item not in eligible:
            invalid += 1
        if item in seen:
            duplicate += 1
        if item in eligible and item not in seen:
            valid += 1
            if item in gains:
                hits += 1
                binary_dcg += 1 / math.log2(rank + 1)
                graded_dcg += gains[item] / math.log2(rank + 1)
        seen.add(item)
    binary_ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(gains)) + 1))
    graded_ideal = sum(
        gain / math.log2(rank + 1)
        for rank, gain in enumerate(sorted(gains.values(), reverse=True)[:k], 1)
    )
    return ListeningScore(
        binary_dcg / binary_ideal if binary_ideal else None,
        graded_dcg / graded_ideal if graded_ideal else None,
        hits / len(gains) if gains else None,
        len(gains),
        len(prefix),
        valid,
        invalid,
        duplicate,
        k - len(prefix),
        max(0, len(response.item_ids) - 20),
    )
