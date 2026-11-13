"""Bounded train-only suffix sequences, keeping every learning user in the index."""

import numpy as np

from backend.infrastructure.recommenders.transition_rules import event_batches


def suffix_sequences(paths, users, items, memory, length, *, first_listen=False):
    # Padding is zero, catalog IDs are shifted by one. Keep an extra training target.
    sequences = np.zeros((len(users), length + 1), dtype=np.int32)
    counts = np.zeros(len(users), dtype=np.int64)
    for user_ids, item_ids, _ in event_batches(
        paths, users, items, memory, first_listen=first_listen
    ):
        starts = np.r_[0, np.flatnonzero(user_ids[1:] != user_ids[:-1]) + 1]
        stops = np.r_[starts[1:], len(user_ids)]
        for start, stop in zip(starts, stops, strict=True):
            user = user_ids[start]
            tail = item_ids[max(start, stop - length - 1) : stop] + 1
            if len(tail) < length + 1:
                sequences[user, : -len(tail)] = sequences[user, len(tail) :]
            sequences[user, -len(tail) :] = tail
            counts[user] += stop - start
    return sequences, {
        "ordered_events": int(counts.sum()),
        "truncated_users": int((counts > length + 1).sum()),
        "training_users": int((counts >= 2).sum()),
        "users_without_ordered_events": int((counts == 0).sum()),
        "users_without_shifted_targets": int((counts < 2).sum()),
    }
