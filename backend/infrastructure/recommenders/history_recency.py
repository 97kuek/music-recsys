"""Validate train-only event identity and align last-play scores to sparse history."""

from datetime import datetime, timedelta

import numpy as np

from backend.infrastructure.recommenders.event_history import validated_event_history


def last_play_weights(
    learning_path,
    events_path,
    expected_sha256,
    prediction_at,
    memory_limit,
    *,
    recent_days=None,
    half_life_days=None,
):
    if recent_days is not None and (type(recent_days) is not int or recent_days <= 0):
        raise ValueError("recent_days must be a positive integer")
    if half_life_days is not None and (type(half_life_days) is not int or half_life_days <= 0):
        raise ValueError("half_life_days must be a positive integer")
    if recent_days is not None and half_life_days is not None:
        raise ValueError("Choose one temporal weighting mode")
    cutoff = datetime.fromisoformat(prediction_at)
    extra_columns = ""
    parameters = []
    if recent_days is not None:
        extra_columns = ",count(*) FILTER (WHERE event_time >= ?) AS recent_plays"
        parameters = [cutoff - timedelta(days=recent_days)]
    if half_life_days is not None:
        extra_columns = ",sum(power(2.0,-epoch(?-event_time)/(86400.0*?))) AS decayed_plays"
        parameters = [cutoff, half_life_days]
    with validated_event_history(
        learning_path,
        events_path,
        expected_sha256,
        prediction_at,
        memory_limit,
        extra_columns=extra_columns,
        extra_parameters=parameters,
    ) as c:
        numerator = "log2(1.0+recent_plays)" if recent_days is not None else "1.0"
        if half_life_days is not None:
            decayed = c.execute(
                "SELECT decayed_plays FROM history ORDER BY user_id,item_id"
            ).fetchnumpy()["decayed_plays"]
            # log1p preserves positive scores for old histories where 1+x rounds to 1.
            weights = np.log1p(decayed) / np.log(2.0)
        else:
            weights = c.execute(
                f"SELECT {numerator}/(1.0+epoch(?-last_play)/86400.0) AS weight "
                "FROM history ORDER BY user_id,item_id",
                [cutoff],
            ).fetchnumpy()["weight"]
    if (
        not np.isfinite(weights).all()
        or (weights < 0).any()
        or (recent_days is None and (weights == 0).any())
    ):
        raise ValueError("Invalid last-play weights")
    return weights
