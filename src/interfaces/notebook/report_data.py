"""Read saved estimates and check that compared reports share observation bins."""


def primary_accuracy(frame, *, window=90, day_weight=1.0):
    return frame[
        (frame.window_days == window)
        & ((frame.metric != "graded_ndcg") | (frame.day_weight == day_weight))
    ]


def accuracy_rows(record, task, metric):
    import pandas as pd

    frame = primary_accuracy(pd.DataFrame(record["result"]["accuracy"]))
    return frame[(frame.task == task) & (frame.metric == metric)].sort_values("k")


def shared_observations(records):
    """Ignore serialization order, but require identical measured distributions."""

    def ordered(record):
        return sorted(
            record["result"]["observations"],
            key=lambda row: (
                row["phase"],
                row["window_days"],
                row["task"],
                row["observation"],
                row["bin"],
            ),
        )

    observed = ordered(records[0])
    if any(ordered(record) != observed for record in records[1:]):
        raise ValueError("Test observation distributions differ")
    return observed
