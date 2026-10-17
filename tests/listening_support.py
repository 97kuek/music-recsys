"""実イベントを使わず、観測事実とmanifestを独立に書くfixture。"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.temporal import TemporalConfig

LEARNING_SCHEMA = pa.schema(
    [
        ("user_id", pa.string()),
        ("item_id", pa.string()),
        ("play_count", pa.int64()),
        ("active_days", pa.int64()),
    ]
)
OBSERVATION_SCHEMA = pa.schema(
    [
        ("user_id", pa.string()),
        ("item_id", pa.string()),
        ("window_days", pa.int64()),
        ("learning_play_count", pa.int64()),
        ("learning_active_days", pa.int64()),
        ("evaluation_play_count", pa.int64()),
        ("evaluation_active_days", pa.int64()),
    ]
)


def write_bundle(path: Path, *, learning=None, observations=None):
    path.mkdir(parents=True)
    learning = (
        learning
        if learning is not None
        else [
            {"user_id": "u", "item_id": "a", "play_count": 2, "active_days": 1},
            {"user_id": "v", "item_id": "b", "play_count": 1, "active_days": 1},
        ]
    )
    observations = (
        observations
        if observations is not None
        else [
            {
                "user_id": "u",
                "item_id": "b",
                "window_days": 90,
                "learning_play_count": 0,
                "learning_active_days": 0,
                "evaluation_play_count": 3,
                "evaluation_active_days": 2,
            }
        ]
    )
    files = {}
    for role, rows, schema in (
        ("learning", learning, LEARNING_SCHEMA),
        ("observations", observations, OBSERVATION_SCHEMA),
    ):
        target = path / f"{role}.parquet"
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), target)
        files[role] = {
            "path": target.name,
            "format": "parquet",
            "row_count": len(rows),
            "byte_size": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    manifest = {
        "schema_version": 2,
        "protocol": "listening-v2",
        "dataset_id": "listening-fixture",
        "phase": "validation",
        "window_days": [90],
        "prediction_at": TemporalConfig().train_end,
        "temporal": asdict(TemporalConfig()),
        "source": {"snapshot_id": "fixture", "split_id": "split"},
        "files": files,
    }
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path


def write_partitions(root):
    from datetime import datetime

    root.mkdir(parents=True)
    rows = [
        ("u", "a", "2017-03-28T09:00:00"),
        ("v", "b", "2017-03-28T09:00:00"),
        ("u", "b", "2017-03-29T00:00:00"),
        ("u", "b", "2017-03-29T01:00:00"),
        ("u", "b", "2017-03-30T00:00:00"),
        ("u", "a", "2017-04-28T00:00:00"),
        ("u", "b", "2017-06-27T00:00:00"),
        ("cold", "a", "2017-04-01T00:00:00"),
        ("u", "cold_item", "2017-04-01T00:00:00"),
        ("u", "b", "2018-09-21T00:00:00"),
    ]
    config, result = TemporalConfig(), {}
    for role in ("train", "validation", "test"):
        selected = []
        for user, item, stamp in rows:
            phase = (
                "train"
                if stamp < config.train_end
                else "validation"
                if stamp < config.validation_end
                else "test"
            )
            if role == phase:
                selected.append(
                    {"user_id": user, "item_id": item, "event_time": datetime.fromisoformat(stamp)}
                )
        target = root / f"{role}.parquet"
        pq.write_table(
            pa.Table.from_pylist(
                selected,
                schema=pa.schema(
                    [
                        ("user_id", pa.string()),
                        ("item_id", pa.string()),
                        ("event_time", pa.timestamp("us")),
                    ]
                ),
            ),
            target,
        )
        result[role] = target
    return result
