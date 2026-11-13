"""Personal replay-frequency control; unseen items tie at zero by global popularity."""

import time

import numpy as np

from backend.infrastructure.recommenders.factors import integer
from backend.infrastructure.recommenders.full_factors import FullFactorModel


class FullHistory(FullFactorModel):
    def __init__(
        self,
        learning_path,
        *,
        use_gpu=False,
        input_memory_mb=2048,
        history_basis="play_count",
        events_path=None,
        events_sha256=None,
        prediction_at=None,
        progress=print,
    ):
        from backend.infrastructure.models.training import capture_training

        integer(input_memory_mb, "input_memory_mb")
        if history_basis not in {
            "play_count",
            "active_days",
            "last_play",
            "recency_log_count",
            "recency_recent_30d",
            "decayed_count_30d",
        }:
            raise ValueError("Unknown history_basis")
        if history_basis in {
            "last_play",
            "recency_log_count",
            "recency_recent_30d",
            "decayed_count_30d",
        } and not all((events_path, events_sha256, prediction_at)):
            raise ValueError("last_play requires training events identity and prediction_at")
        if type(use_gpu) is not bool:
            raise ValueError("use_gpu must be boolean")
        capture_training(self, locals())
        started = time.perf_counter()
        self.history_weights = self.load_learning(
            learning_path,
            memory_limit=f"{input_memory_mb}MB",
            value_column="play_count"
            if history_basis
            in {"last_play", "recency_log_count", "recency_recent_30d", "decayed_count_30d"}
            else history_basis,
        )
        if history_basis in {
            "last_play",
            "recency_log_count",
            "recency_recent_30d",
            "decayed_count_30d",
        }:
            from backend.infrastructure.recommenders.history_recency import last_play_weights

            progress("Validating train events and aggregating last-play timestamps")
            weights = last_play_weights(
                learning_path,
                events_path,
                events_sha256,
                prediction_at,
                f"{input_memory_mb}MB",
                recent_days=30 if history_basis == "recency_recent_30d" else None,
                half_life_days=30 if history_basis == "decayed_count_30d" else None,
            )
            if len(weights) != self.history_weights.nnz:
                raise ValueError("Last-play/history alignment differs")
            if history_basis == "recency_log_count":
                weights *= np.log2(1.0 + self.history_weights.data)
            self.history_weights.data = weights
        self.execution = dict(
            backend="scipy",
            dtype="float64",
            training_device="cpu",
            solver=f"exact per-user learning {history_basis}; no fitted parameters",
            input_transform=f"raw learning {history_basis}",
            random_computation=False,
        )
        if history_basis in {
            "last_play",
            "recency_log_count",
            "recency_recent_30d",
            "decayed_count_30d",
        }:
            self.execution.update(
                events_path=events_path,
                events_sha256=events_sha256,
                prediction_at=prediction_at,
                input_transform=(
                    "log1p(sum(2**(-event_age_days/30)))/ln(2); all learning events"
                    if history_basis == "decayed_count_30d"
                    else "log2(1+plays in [prediction_at-30d,prediction_at))/(1+days since last train play); zero if no recent plays"
                    if history_basis == "recency_recent_30d"
                    else "log2(1+learning play_count)/(1+days since last train play)"
                    if history_basis == "recency_log_count"
                    else "1/(1+days since last train play); descending last timestamp"
                ),
            )
        if use_gpu:
            from cupyx.scipy.sparse import csr_matrix

            from backend.infrastructure.recommenders.gpu_runtime import (
                gpu_metadata,
                require_idle_gpu,
            )

            progress(require_idle_gpu())
            self.history_weights = csr_matrix(self.history_weights)
            self.execution.update(
                backend="cupy/cupyx CUDA", training_device="cuda", **gpu_metadata()
            )
        self.training_seconds = time.perf_counter() - started
        self.cache_hit = False

    def gpu_score_batch(self, user_ids):
        import cupy as cp
        from cupyx.scipy.sparse import csr_matrix

        if not isinstance(self.history_weights, csr_matrix):
            raise ValueError("GPU history model required")
        return self.history_weights[cp.asarray(user_ids)].toarray()

    def rank(self, user, history, task, top_k=20):
        if task not in {"new", "repeat"}:
            raise ValueError("task must be new or repeat")
        scores = self.history_weights[self.users[user]].toarray().ravel()
        if self.execution["training_device"] == "cuda":
            import cupy as cp

            scores = cp.asnumpy(scores)
        allowed = np.array([(i in history) == (task == "repeat") for i in self.items])
        indices = np.flatnonzero(allowed)
        order = np.lexsort((indices, -self.plays[indices], -scores[indices]))[:top_k]
        return self.items[indices[order]].tolist()
