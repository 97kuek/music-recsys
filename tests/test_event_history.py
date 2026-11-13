from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.recommenders.event_history import validated_event_history


def test_event_identity_rechecks_source_after_consumer(tmp_path):
    learning, events = tmp_path / "learning.parquet", tmp_path / "events.parquet"
    pq.write_table(pa.Table.from_pylist([dict(user_id="u", item_id="i", play_count=1)]), learning)
    rows = [dict(user_id="u", item_id="i", event_time=datetime(2020, 1, 1))]
    pq.write_table(pa.Table.from_pylist(rows), events)
    digest = file_sha256(events)
    with pytest.raises(ValueError, match="changed during aggregation"):
        with validated_event_history(
            learning, events, digest, "2020-01-03T00:00:00", "128MB"
        ) as connection:
            assert connection.execute("SELECT n FROM history").fetchall() == [(1,)]
            rows[0]["event_time"] = datetime(2020, 1, 2)
            pq.write_table(pa.Table.from_pylist(rows), events)
