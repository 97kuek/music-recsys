"""v2の全量向け生成を、6百万イベントと128MB上限で検証する。"""

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("MUSIC_RECSYS_TEST_SCALE") != "1",
    reason="opt-in listening bounded-memory regression",
)
def test_listening_generation_is_bounded_before_model_loading(tmp_path, monkeypatch):
    import duckdb
    import pyarrow.parquet as pq

    from src.listening_data import load_listening_dataset
    from src.listening_export import export_listening_phase
    from src.listening_types import ListeningConfig
    from src.temporal import TemporalConfig

    files = {role: tmp_path / f"{role}.parquet" for role in ("train", "validation", "test")}
    connect = duckdb.connect
    with connect(config={"memory_limit": "512MB", "threads": 2}) as connection:
        connection.execute(
            """COPY (SELECT 'user_' || md5((i%10000)::VARCHAR) user_id,
            'item_' || lpad((i%100000)::VARCHAR,12,'0') item_id,
            TIMESTAMP '2016-01-01' + i * INTERVAL 1 SECOND event_time
            FROM range(6000000) t(i)) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 65536)""",
            [str(files["train"])],
        )
        for role in ("validation", "test"):
            connection.execute(
                "COPY (SELECT * FROM read_parquet($source) WHERE false) TO $target (FORMAT PARQUET)",
                {"source": str(files["train"]), "target": str(files[role])},
            )

    def limited(*args, config=None, **kwargs):
        return connect(*args, config={**(config or {}), "memory_limit": "128MB"}, **kwargs)

    monkeypatch.setattr(duckdb, "connect", limited)
    bundle = tmp_path / "bundle"
    export_listening_phase(
        files, bundle, TemporalConfig(), phase="validation", provenance={}, memory_limit="128MB"
    )
    assert pq.ParquetFile(bundle / "learning.parquet").metadata.num_rows == 100_000
    assert pq.ParquetFile(bundle / "observations.parquet").metadata.num_rows == 0
    with connect() as connection:
        assert (
            connection.execute(
                "SELECT sum(play_count) FROM read_parquet(?)", [str(bundle / "learning.parquet")]
            ).fetchone()[0]
            == 6_000_000
        )
    with pytest.raises(ValueError, match="2000 users"):
        load_listening_dataset(bundle, ListeningConfig())
