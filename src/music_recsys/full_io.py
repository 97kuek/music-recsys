"""Streaming input and bounded Parquet detail writers for full evaluation."""

from itertools import groupby


def user_rows(path):
    import pyarrow.parquet as pq

    def rows():
        for batch in pq.ParquetFile(path).iter_batches(batch_size=32768):
            yield from zip(*(column.to_pylist() for column in batch.columns), strict=True)

    for user, records in groupby(rows(), key=lambda row: row[0]):
        yield user, list(records)


class DetailWriter:
    def __init__(self, path):
        self.path, self.rows, self.writer = path, [], None

    def add(self, row):
        self.rows.append(row)
        if len(self.rows) >= 4096:
            self.flush()

    def flush(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        if self.rows:
            table = pa.Table.from_pylist(self.rows)
            if self.writer is None:
                self.writer = pq.ParquetWriter(self.path, table.schema, compression="zstd")
            self.writer.write_table(table)
            self.rows.clear()

    def close(self):
        self.flush()
        if self.writer is not None:
            self.writer.close()
