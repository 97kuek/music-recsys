"""外部ソートと小さなArrow batchで、重複を含むイベント全件を照合する。"""

import tempfile
from contextlib import closing
from pathlib import Path

from music_recsys.ingestion.types import ValidationError


def assert_same_events(connection, left_query: str, right_query: str, temporary_root: Path) -> None:
    """内部SQLの2結果をmultisetとして厳密比較する。hashの一致では代用しない。

    ソートを順番に実行し、複数の巨大なhash/window演算を同時に保持しない。
    SQLは呼出側の固定文だけを渡す。Parquetの入力値はview経由で参照する。
    """
    import pyarrow.parquet as pq

    threads = connection.execute("SELECT current_setting('threads')").fetchone()[0]
    connection.execute("SET threads=1")
    try:
        with tempfile.TemporaryDirectory(prefix="event-compare-", dir=temporary_root) as directory:
            expected = Path(directory) / "expected.parquet"
            connection.execute(
                f"""COPY (SELECT * FROM ({left_query})
                ORDER BY user_id,item_id,event_time) TO ?
                (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 65536)""",
                [str(expected)],
            )
            left = iter(pq.ParquetFile(expected).iter_batches(batch_size=65536))
            with closing(
                connection.sql(
                    f"SELECT * FROM ({right_query}) ORDER BY user_id,item_id,event_time"
                ).to_arrow_reader(batch_size=65536)
            ) as right:
                a, b = next(left, None), next(right, None)
                while a is not None and b is not None:
                    count = min(a.num_rows, b.num_rows)
                    if not a.slice(0, count).equals(b.slice(0, count)):
                        raise ValidationError("partition multiset differs from source events")
                    a = a.slice(count) if a.num_rows > count else next(left, None)
                    b = b.slice(count) if b.num_rows > count else next(right, None)
                if a is not None or b is not None:
                    raise ValidationError("partition multiset differs from source events")
    finally:
        connection.execute(f"SET threads={int(threads)}")
