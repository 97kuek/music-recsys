"""SeqRules-inspired directional transitions with continuous temporal user profiles."""

import time

import numpy as np

from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.recommenders.factors import integer, number
from backend.infrastructure.recommenders.full_factors import FullFactorModel
from backend.infrastructure.recommenders.history_recency import last_play_weights
from backend.infrastructure.recommenders.transition_rules import (
    accumulate_rules,
    event_batches,
    normalize_rules,
    prune_rules,
)


class FullTransition(FullFactorModel):
    def __init__(
        self,
        learning_path,
        *,
        events_path,
        events_sha256,
        prediction_at,
        use_gpu=False,
        input_memory_mb=32768,
        distance=5,
        neighbors=100,
        max_gap_days=1,
        symmetric=False,
        transition_events="all",
        degree_power=0.0,
        progress=print,
    ):
        from scipy import sparse

        from backend.infrastructure.models.training import capture_training

        for name, value in dict(
            input_memory_mb=input_memory_mb,
            distance=distance,
            neighbors=neighbors,
            max_gap_days=max_gap_days,
        ).items():
            integer(value, name)
        if type(use_gpu) is not bool or type(symmetric) is not bool:
            raise ValueError("use_gpu and symmetric must be boolean")
        if transition_events not in {"all", "first_listen"}:
            raise ValueError("transition_events must be all or first_listen")
        number(degree_power, "degree_power")
        if degree_power > 1:
            raise ValueError("degree_power must be at most 1")
        if not isinstance(events_path, (list, tuple)) or not events_path:
            raise ValueError("Explicit event source list required")
        capture_training(self, locals())
        started = time.perf_counter()
        xp = np
        self.execution = dict(backend="scipy", training_device="cpu", dtype="float64")
        if use_gpu:
            from backend.infrastructure.recommenders.gpu_runtime import (
                gpu_metadata,
                require_idle_gpu,
            )

            progress(require_idle_gpu())
            import cupy as xp
            from cupyx.scipy import sparse

            self.execution.update(
                backend="cupy/cupyx CUDA", training_device="cuda", **gpu_metadata()
            )
        memory = f"{input_memory_mb}MB"
        self.histories = self.load_learning(learning_path, memory_limit=memory)
        progress("Validating event identity and building 30-day decay profiles")
        self.histories.data = last_play_weights(
            learning_path,
            events_path,
            events_sha256,
            prediction_at,
            memory,
            half_life_days=30,
        )
        progress("Sorting unambiguous training events; aggregating transition rules")

        def batches():
            seen = 0
            for batch in event_batches(
                events_path,
                self.users,
                self.index,
                memory,
                first_listen=transition_events == "first_listen",
            ):
                seen += len(batch[0])
                if seen % (262144 * 20) == 0:
                    progress(f"Transition events processed: {seen:,}")
                yield batch

        counts, statistics = accumulate_rules(
            batches(),
            len(self.items),
            distance=distance,
            max_gap_days=max_gap_days,
            xp=xp,
            sparse=sparse,
        )
        if symmetric:
            counts = (counts + counts.T) * 0.5
        counts = normalize_rules(counts, degree_power, xp=xp, sparse=sparse)
        self.rules = prune_rules(counts, neighbors, xp=xp, sparse=sparse)
        self.histories = sparse.csr_matrix(self.histories)
        for path, digest in zip(events_path, events_sha256, strict=True):
            if file_sha256(path) != digest:
                raise ValueError("Training events changed during transition aggregation")
        self.execution.update(
            solver="sparse weighted transition counts; deterministic row top-neighbors",
            input_transform="log1p(sum(exp2(-event_age_days/30)))/ln(2)",
            transition_weight="1 / retained event position distance; self-transitions included",
            simultaneous_events="exclude all events at nonunique (user,timestamp) from transitions only",
            symmetric=symmetric,
            transition_events=transition_events,
            degree_power=degree_power,
            normalization="T_ij / (outgoing_mass_i * incoming_mass_j)^degree_power before pruning",
            max_gap_days=max_gap_days,
            distance=distance,
            neighbors=neighbors,
            retained_rules=int(self.rules.nnz),
            random_computation=False,
            bitwise_reproducible=False,
            **statistics,
        )
        if use_gpu:
            xp.cuda.Stream.null.synchronize()
        self.training_seconds = time.perf_counter() - started
        self.cache_hit = False

    def gpu_score_batch(self, user_ids):
        import cupy as cp

        if self.execution["training_device"] != "cuda":
            raise ValueError("GPU transition model required")
        return (self.histories[cp.asarray(user_ids)] @ self.rules).toarray()

    def rank(self, user, history, task, top_k=20):
        if task not in {"new", "repeat"}:
            raise ValueError("task must be new or repeat")
        scores = (self.histories[self.users[user]] @ self.rules).toarray().ravel()
        if self.execution["training_device"] == "cuda":
            import cupy as cp

            scores = cp.asnumpy(scores)
        candidates = np.flatnonzero(
            np.array([(item in history) == (task == "repeat") for item in self.items])
        )
        order = np.lexsort((candidates, -self.plays[candidates], -scores[candidates]))[:top_k]
        return self.items[candidates[order]].tolist()
