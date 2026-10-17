"""全件評価と同じ集約統計による、配信用Popularity。"""

import numpy as np

from src.model_artifacts import capture_training
from src.recommenders.popularity import validate_basis


class FullPopularity:
    def __init__(self, learning_path, *, basis="play_count"):
        import duckdb

        validate_basis(basis)
        capture_training(self, locals())
        with duckdb.connect(config={"threads": 2, "memory_limit": "2GB"}) as connection:
            rows = connection.execute(
                "SELECT item_id,sum(play_count),count(*) FROM read_parquet(?) GROUP BY item_id ORDER BY item_id",
                [str(learning_path)],
            ).fetchall()
        self.items = np.asarray([row[0] for row in rows], dtype=str)
        self.plays = np.asarray([row[1] for row in rows], dtype=np.float64)
        self.scores = np.asarray(
            [row[1 if basis == "play_count" else 2] for row in rows], dtype=np.float64
        )
