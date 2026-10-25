"""Typed candidate Parquet IO and alignment checks shared by generation and evaluation."""

from itertools import islice, zip_longest
from pathlib import Path

import numpy as np

from src.infrastructure.datasets.full_io import DetailWriter


class CandidateWriter(DetailWriter):
    def __init__(self, path):
        import pyarrow as pa

        schema = pa.schema(
            [
                ("user_id", pa.string()),
                ("task", pa.string()),
                ("item_indices", pa.list_(pa.int32())),
                ("raw_scores", pa.list_(pa.float64())),
                ("source_ranks", pa.list_(pa.int32())),
                ("candidate_count", pa.int32()),
            ]
        )
        super().__init__(path, schema=schema, compression_level=9)


def candidate_batches(references, *, users=64):
    import pyarrow.parquet as pq

    def stream(reference_):
        for batch in pq.ParquetFile(
            Path(reference_["directory"]) / "candidates.parquet"
        ).iter_batches(batch_size=2 * users):
            yield from batch.to_pylist()

    rows = zip_longest(*(stream(r) for r in references))
    previous = None
    while batch := list(islice(rows, 2 * users)):
        if len(batch) % 2:
            raise ValueError("Candidate artifact has a partial user")
        for position, request in enumerate(batch):
            if any(r is None for r in request):
                raise ValueError("Candidate source population mismatch")
            key = request[0]["user_id"], request[0]["task"]
            if key[1] != ("new" if position % 2 == 0 else "repeat") or any(
                (r["user_id"], r["task"]) != key for r in request
            ):
                raise ValueError("Candidate task/user order mismatch")
            if position % 2 == 0:
                if previous is not None and key[0] <= previous:
                    raise ValueError("Candidate users must be unique and sorted")
                previous = key[0]
            elif batch[position - 1][0]["user_id"] != key[0]:
                raise ValueError("Missing candidate task")
            if len({r["candidate_count"] for r in request}) != 1:
                raise ValueError("Eligible catalog counts differ")
            for row in request:
                n = len(row["item_indices"])
                if (
                    len(set(row["item_indices"])) != n
                    or row["source_ranks"] != list(range(1, n + 1))
                    or len(row["raw_scores"]) != n
                    or not np.isfinite(row["raw_scores"]).all()
                ):
                    raise ValueError("Malformed raw candidates")
        yield batch
