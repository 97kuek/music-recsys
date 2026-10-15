"""v2 bundleを検証し、小規模モデル実行の上限確認後に学習情報を展開する。"""

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from music_recsys.data import Interaction
from music_recsys.ingestion.types import canonical_json
from music_recsys.listening_types import ListeningDataset, PlaybackObservation
from music_recsys.temporal import TemporalConfig
from music_recsys.types import RecommendationRequest, validate_id


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parquet_schemas():
    import pyarrow as pa

    learning = pa.schema(
        [
            ("user_id", pa.string()),
            ("item_id", pa.string()),
            ("play_count", pa.int64()),
            ("active_days", pa.int64()),
        ]
    )
    observations = pa.schema(
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
    return {"learning": learning, "observations": observations}


def read_bundle(bundle):
    """manifestと全参照のhash・スキーマを検証する。観測行の値は読まない。"""
    import pyarrow.parquet as pq

    bundle = Path(bundle).resolve()
    raw = (bundle / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    if (
        type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 2
        or manifest.get("protocol") != "listening-v2"
    ):
        raise ValueError("bundle must explicitly declare listening-v2 schema 2")
    validate_id(manifest["dataset_id"])
    temporal = TemporalConfig(**manifest["temporal"])
    phase = manifest["phase"]
    if phase not in {"validation", "test"}:
        raise ValueError("invalid phase")
    prediction = temporal.train_end if phase == "validation" else temporal.validation_end
    observed_prediction = datetime.fromisoformat(manifest["prediction_at"])
    if observed_prediction.tzinfo is not None or observed_prediction != datetime.fromisoformat(
        prediction
    ):
        raise ValueError("prediction boundary mismatch")
    windows = manifest["window_days"]
    if (
        not isinstance(windows, list)
        or not windows
        or any(type(w) is not int or w <= 0 for w in windows)
        or len(set(windows)) != len(windows)
    ):
        raise ValueError("invalid evaluation windows")
    if phase == "validation" and datetime.fromisoformat(prediction) + timedelta(
        days=max(windows)
    ) > datetime.fromisoformat(temporal.validation_end):
        raise ValueError("Validation window crosses Test boundary")
    if set(manifest["files"]) != {"learning", "observations"}:
        raise ValueError("invalid bundle file roles")
    paths = {}
    for role, schema in parquet_schemas().items():
        ref = manifest["files"][role]
        relative = Path(ref["path"])
        path = (bundle / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(bundle):
            raise ValueError("artifact path must stay inside bundle")
        if (
            ref.get("format") != "parquet"
            or not path.is_file()
            or type(ref.get("byte_size")) is not int
            or type(ref.get("row_count")) is not int
            or ref["row_count"] < 0
            or path.stat().st_size != ref["byte_size"]
            or file_sha256(path) != ref["sha256"]
        ):
            raise ValueError("artifact size / checksum mismatch")
        metadata = pq.ParquetFile(path)
        if (
            not metadata.schema_arrow.equals(schema, check_metadata=False)
            or metadata.metadata.num_rows != ref["row_count"]
        ):
            raise ValueError("artifact schema / row count mismatch")
        paths[role] = path
    return manifest, paths


def validate_bundle(bundle):
    """観測をPython化せず、一意性・warm条件・回数の整合性を確認する。"""
    import duckdb

    manifest, paths = read_bundle(bundle)
    windows = manifest["window_days"]
    with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as connection:
        for role, path in paths.items():
            connection.read_parquet(str(path)).create_view(role)
        for role in paths:
            id_error = connection.execute(
                f"SELECT count(*) FROM {role} WHERE user_id IS NULL OR item_id IS NULL OR trim(user_id)='' OR trim(item_id)=''"
            ).fetchone()[0]
            if id_error:
                raise ValueError("invalid IDs")
        if connection.execute(
            "SELECT count(*) FROM learning WHERE play_count IS NULL OR active_days IS NULL OR play_count<=0 OR active_days<=0 OR active_days>play_count"
        ).fetchone()[0]:
            raise ValueError("invalid learning counts")
        if connection.execute(
            "SELECT count(*) FROM (SELECT user_id,item_id FROM learning GROUP BY ALL HAVING count(*)>1)"
        ).fetchone()[0]:
            raise ValueError("duplicate learning key")
        if connection.execute(
            "SELECT count(*) FROM (SELECT user_id,item_id,window_days FROM observations GROUP BY ALL HAVING count(*)>1)"
        ).fetchone()[0]:
            raise ValueError("duplicate observation key")
        if connection.execute(
            """SELECT count(*) FROM observations WHERE window_days IS NULL OR window_days NOT IN (SELECT unnest(?)) OR learning_play_count IS NULL OR learning_active_days IS NULL OR evaluation_play_count IS NULL OR evaluation_active_days IS NULL OR learning_play_count<0 OR learning_active_days<0 OR learning_active_days>learning_play_count OR (learning_play_count=0)<>(learning_active_days=0) OR evaluation_play_count<=0 OR evaluation_active_days<=0 OR evaluation_active_days>evaluation_play_count""",
            [windows],
        ).fetchone()[0]:
            raise ValueError("invalid observation counts / windows")
        if connection.execute("""SELECT count(*) FROM observations o
            LEFT JOIN learning l USING(user_id,item_id)
            LEFT JOIN (SELECT DISTINCT user_id FROM learning) u USING(user_id)
            LEFT JOIN (SELECT DISTINCT item_id FROM learning) i USING(item_id)
            WHERE u.user_id IS NULL OR i.item_id IS NULL OR o.learning_play_count<>coalesce(l.play_count,0) OR o.learning_active_days<>coalesce(l.active_days,0)""").fetchone()[
            0
        ]:
            raise ValueError("cold observation / learning count mismatch")
        if connection.execute(
            """SELECT count(*) FROM (SELECT evaluation_play_count n,evaluation_active_days d,lag(evaluation_play_count) OVER w previous_n,lag(evaluation_active_days) OVER w previous_d FROM observations WINDOW w AS (PARTITION BY user_id,item_id ORDER BY window_days)) WHERE n<previous_n OR d<previous_d"""
        ).fetchone()[0]:
            raise ValueError("nested windows must have nondecreasing counts")
        if connection.execute(
            """SELECT count(*) FROM
            (SELECT user_id,item_id,min(window_days) first_window FROM observations GROUP BY user_id,item_id) p
            CROSS JOIN (SELECT unnest(?) AS window_days) w
            LEFT JOIN observations o ON o.user_id=p.user_id AND o.item_id=p.item_id AND o.window_days=w.window_days
            WHERE w.window_days>=p.first_window AND o.user_id IS NULL""",
            [windows],
        ).fetchone()[0]:
            raise ValueError("positive observation is missing from a longer cumulative window")
        users, events, items = connection.execute(
            "SELECT count(DISTINCT user_id),coalesce(sum(play_count),0),count(DISTINCT item_id) FROM learning"
        ).fetchone()
        if not users or not items:
            raise ValueError("learning data must not be empty")
    return manifest, paths, {"users": int(users), "events": int(events), "items": int(items)}


def load_listening_dataset(bundle, config):
    import duckdb

    manifest, paths, counts = validate_bundle(bundle)
    if counts["users"] > 2000 or counts["events"] > 5_000_000:
        raise ValueError(
            "listening model evaluation is limited to 2000 users / 5 million learning events"
        )
    if not set(config.window_days).issubset(manifest["window_days"]):
        raise ValueError("requested evaluation window is not in bundle")
    history, events = defaultdict(list), []
    with duckdb.connect(config={"memory_limit": "2GB", "threads": 2}) as connection:
        connection.read_parquet(str(paths["learning"])).create_view("learning")
        catalog = tuple(
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT item_id FROM learning ORDER BY item_id"
            ).fetchall()
        )
        cursor = connection.execute(
            "SELECT user_id,item_id,play_count FROM learning ORDER BY user_id,item_id"
        )
        while rows := cursor.fetchmany(65536):
            for user, item, count in rows:
                history[user].append(item)
                events.extend(Interaction(user, item) for _ in range(count))
    requests = []
    for user, items in history.items():
        heard = set(items)
        for task in ("new", "repeat"):
            candidates = tuple(item for item in catalog if (item in heard) == (task == "repeat"))
            requests.append(
                RecommendationRequest(
                    f"{task}:{user}", user, tuple(items), candidates, 20, task=task
                )
            )
    checksum = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    return ListeningDataset(
        manifest["dataset_id"],
        catalog,
        tuple(events),
        tuple(requests),
        paths["observations"],
        manifest,
        checksum,
    )


def read_user_observations(connection, user_id):
    rows = connection.execute(
        "SELECT user_id,item_id,window_days,learning_play_count,learning_active_days,evaluation_play_count,evaluation_active_days FROM observations WHERE user_id=? ORDER BY item_id,window_days",
        [user_id],
    ).fetchall()
    return tuple(PlaybackObservation(*row) for row in rows)
