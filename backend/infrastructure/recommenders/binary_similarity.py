"""Pearson correlation of two 0/1 columns over all learning users (phi coefficient)."""

import numpy as np


def pearson(shared, left, right, population, *, xp=np):
    shared, left, right = (xp.asarray(v, dtype=xp.float64) for v in (shared, left, right))
    denominator = xp.sqrt(left * (population - left) * right * (population - right))
    # Constant columns have undefined correlation; graph construction excludes them
    return xp.where(
        denominator > 0,
        (population * shared - left * right) / xp.where(denominator > 0, denominator, 1),
        xp.nan,
    )
