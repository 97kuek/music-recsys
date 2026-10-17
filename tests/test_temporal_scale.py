"""全量で見つかったOOMを、合成データと小さなメモリ上限で再現する。"""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_SCALE") != "1", reason="opt-in bounded-memory regression"
)
def test_temporal_roundtrip_spills_with_long_ids(tmp_path, monkeypatch):
    import duckdb

    from src.temporal import TemporalConfig, prepare_temporal

    source = tmp_path / "events.parquet"
    connect = duckdb.connect
    with connect(config={"memory_limit": "512MB", "threads": 2}) as c:
        c.execute(
            """COPY (SELECT 'user_' || md5((i%10000)::VARCHAR) AS user_id,
          'item_' || lpad((i%100000)::VARCHAR,12,'0') AS item_id,
          TIMESTAMP '2016-01-01' + i * INTERVAL 1 SECOND AS event_time
          FROM range(6000000) t(i)) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 65536)""",
            [str(source)],
        )

    def limited(*args, config=None, **kwargs):
        return connect(*args, config={**(config or {}), "memory_limit": "128MB"}, **kwargs)

    monkeypatch.setattr(duckdb, "connect", limited)
    result = prepare_temporal(source, tmp_path / "split", TemporalConfig(), provenance={})
    assert result.summary["partitions"]["train"]["events"] == 6_000_000
    assert result.summary["source"]["users"] == 10_000
    assert result.summary["evaluation"]["test"]["evaluable_users"] == 0
    assert result.summary["leakage_checks"]["passed"] is True
    from dataclasses import asdict

    from src.ingestion.split_validation import validate_saved_partitions

    validate_saved_partitions(result.files, asdict(TemporalConfig()), result.summary)
