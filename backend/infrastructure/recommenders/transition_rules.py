"""Stream unambiguous train-only event sequences and accumulate directional rules."""

import numpy as np

from backend.infrastructure.recommenders.factors import number


def normalize_rules(counts, power, *, xp, sparse):
    """Scale by pre-pruning outgoing/incoming mass; zero-degree nodes stay zero."""
    number(power, "degree_power")
    if power > 1:
        raise ValueError("degree_power must be at most 1")
    if power == 0:
        return counts
    rows = xp.asarray(counts.sum(axis=1)).ravel()
    columns = xp.asarray(counts.sum(axis=0)).ravel()
    left, right = xp.zeros_like(rows), xp.zeros_like(columns)
    left[rows > 0] = rows[rows > 0] ** -power
    right[columns > 0] = columns[columns > 0] ** -power
    return (sparse.diags(left) @ counts @ sparse.diags(right)).tocsr()


def event_batches(paths, users, items, memory_limit, batch_size=262144, *, first_listen=False):
    """Use integer IDs before sorting; never spill a large intermediate to disk."""
    import duckdb
    import pyarrow as pa

    with duckdb.connect(
        config={"threads": 2, "memory_limit": memory_limit, "temp_directory": ""}
    ) as connection:
        connection.register(
            "user_index", pa.table({"user_id": list(users), "ui": list(users.values())})
        )
        connection.register(
            "item_index", pa.table({"item_id": list(items), "ii": list(items.values())})
        )
        mapped = (
            "SELECT ui,ii,event_time FROM read_parquet(?) JOIN user_index USING(user_id) "
            "JOIN item_index USING(item_id)"
        )
        first_filter = ""
        if first_listen:
            # Determine first occurrence BEFORE removing ambiguous timestamps; a later
            # repeat must never become a supposed first listen after that removal.
            mapped = (
                f"SELECT *,min(event_time) OVER (PARTITION BY ui,ii) AS first_at FROM ({mapped})"
            )
            first_filter = " AND event_time=min(first_at)"
        reader = connection.execute(
            "SELECT ui::INTEGER AS ui,min(ii)::INTEGER AS ii,epoch_us(event_time) AS ts "
            f"FROM ({mapped}) GROUP BY ui,event_time HAVING count(*)=1 "
            f"{first_filter} "
            "ORDER BY ui,ts",
            [[str(p) for p in paths]],
        ).to_arrow_reader(batch_size)
        for batch in reader:
            yield tuple(column.to_numpy() for column in batch.columns)


def accumulate_rules(batches, n_items, *, distance, max_gap_days, xp, sparse):
    """Each retained target is counted once, including across stream boundaries."""
    counts = sparse.csr_matrix((n_items, n_items), dtype=xp.float64)
    carry = tuple(np.empty(0, dtype=np.int64) for _ in range(3))
    retained, pairs = 0, 0
    for batch in batches:
        previous = len(carry[0])
        arrays = tuple(np.concatenate([a, b]) for a, b in zip(carry, batch, strict=True))
        users, items, timestamps = (xp.asarray(a) for a in arrays)
        rows, columns, values = [], [], []
        for lag in range(1, distance + 1):
            target = xp.arange(max(lag, previous), len(users))
            source = target - lag
            gap = timestamps[target] - timestamps[source]
            mask = (
                (users[target] == users[source])
                & (gap > 0)
                & (gap <= max_gap_days * 86400 * 1000000)
            )
            rows.append(items[source[mask]])
            columns.append(items[target[mask]])
            values.append(xp.full(int(mask.sum()), 1.0 / lag, dtype=xp.float64))
        if rows:
            row, column, value = map(xp.concatenate, (rows, columns, values))
            pairs += len(value)
            if len(value):
                counts = (
                    counts + sparse.coo_matrix((value, (row, column)), shape=counts.shape).tocsr()
                )
        retained += len(batch[0])
        carry = tuple(a[-distance:].copy() for a in arrays)
    counts.sum_duplicates()
    counts.sort_indices()
    return counts, dict(retained_transition_events=retained, transition_pairs=pairs)


def prune_rules(counts, neighbors, *, xp, sparse):
    """Keep positive row top-neighbors, resolving equal weights by item ID."""
    width = min(neighbors, counts.shape[1])
    indices, data = [], []
    for start in range(0, counts.shape[0], 128):
        block = counts[start : start + 128].toarray()
        selected = xp.argsort(-block, axis=1, kind="stable")[:, :width]
        indices.append(selected.ravel())
        data.append(xp.take_along_axis(block, selected, axis=1).ravel())
    result = sparse.csr_matrix(
        (xp.concatenate(data), xp.concatenate(indices), xp.arange(counts.shape[0] + 1) * width),
        shape=counts.shape,
    )
    result.eliminate_zeros()
    result.sort_indices()
    return result
