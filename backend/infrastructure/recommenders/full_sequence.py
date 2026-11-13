"""Train-only eSASRec-style network; exact full-catalog CPU/CUDA scoring."""

import time
from pathlib import Path

import numpy as np

from backend.infrastructure.datasets.listening_data import file_sha256
from backend.infrastructure.recommenders.event_history import validated_event_history
from backend.infrastructure.recommenders.factors import integer, number
from backend.infrastructure.recommenders.full_factors import FullFactorModel
from backend.infrastructure.recommenders.sequence_input import suffix_sequences


class FullSequence(FullFactorModel):
    def __init__(
        self,
        learning_path,
        *,
        events_path,
        events_sha256,
        prediction_at,
        seed=0,
        use_gpu=True,
        input_memory_mb=32768,
        sequence_length=100,
        factors=64,
        heads=2,
        blocks=2,
        dropout=0.2,
        negatives=256,
        batch_size=128,
        epochs=100,
        learning_rate=0.001,
        sequence_events="all",
        checkpoint_path=None,
        probe_batches=None,
        progress=print,
    ):
        for name, value in dict(
            input_memory_mb=input_memory_mb,
            sequence_length=sequence_length,
            factors=factors,
            heads=heads,
            blocks=blocks,
            negatives=negatives,
            batch_size=batch_size,
            epochs=epochs,
        ).items():
            integer(value, name)
        number(dropout, "dropout")
        number(learning_rate, "learning_rate", positive=True)
        if dropout >= 1 or factors % heads:
            raise ValueError("dropout must be below 1; factors must be divisible by heads")
        if type(seed) is not int or seed < 0 or type(use_gpu) is not bool:
            raise ValueError("non-negative integer seed and boolean use_gpu required")
        if sequence_events not in {"all", "first_listen"}:
            raise ValueError("sequence_events must be all or first_listen")
        if not isinstance(events_path, (list, tuple)) or not events_path:
            raise ValueError("Explicit event source list required")
        if checkpoint_path is not None and Path(checkpoint_path).exists():
            raise FileExistsError("Preserve existing sequence checkpoint")
        if probe_batches is not None:
            integer(probe_batches, "probe_batches")
            if checkpoint_path is not None:
                raise ValueError("Resource probe cannot publish a trained checkpoint")
        import torch

        from backend.infrastructure.recommenders.sequence_network import SequenceNetwork

        started = time.perf_counter()
        self.device = torch.device("cuda" if use_gpu else "cpu")
        self.execution = dict(
            backend="pytorch",
            training_device=str(self.device),
            torch=torch.__version__,
            dtype="float32",
            seed=seed,
            solver="Adam; betas=(0.9,0.98); shifted sequence sampled softmax",
            sequence_events=sequence_events,
            sequence_length=sequence_length,
            negative_sampling="uniform catalog with replacement; collisions retained; no logQ",
            padding="left zero padding; padding keys masked; diagonal open for padded queries",
            simultaneous_events="exclude all nonunique user,timestamp events from sequences",
            no_sequence="zero score; explicit training-popularity ties",
            random_computation=True,
            bitwise_reproducible=False,
            mode="resource_probe" if probe_batches is not None else "full_training",
        )
        if use_gpu:
            from backend.infrastructure.recommenders.gpu_runtime import (
                gpu_metadata,
                require_idle_gpu,
            )

            progress(require_idle_gpu())
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA PyTorch required; install sequence-cuda, no CPU fallback")
            self.execution.update(
                gpu_metadata(),
                cuda=torch.version.cuda,
                cudnn=torch.backends.cudnn.version(),
                tf32_matmul=torch.backends.cuda.matmul.allow_tf32,
            )
            torch.cuda.reset_peak_memory_stats()
        memory = f"{input_memory_mb}MB"
        self.histories = self.load_learning(learning_path, memory_limit=memory)
        progress("Validating train-only event identity before sequence construction")
        with validated_event_history(
            learning_path, events_path, events_sha256, prediction_at, memory
        ):
            pass
        sequences, statistics = suffix_sequences(
            events_path,
            self.users,
            self.index,
            memory,
            sequence_length,
            first_listen=sequence_events == "first_listen",
        )
        if not statistics["training_users"]:
            raise ValueError("No unambiguous shifted-sequence training targets")
        self.execution.update(statistics)
        self.sequences = torch.as_tensor(sequences, dtype=torch.long, device=self.device)
        eligible = torch.nonzero((self.sequences != 0).sum(1) >= 2).ravel()
        progress(f"Training {len(eligible):,} users; evaluating all {len(self.users):,} users")
        with torch.random.fork_rng(devices=[torch.cuda.current_device()] if use_gpu else []):
            torch.manual_seed(seed)
            self.network = SequenceNetwork(
                len(self.items), sequence_length, factors, heads, blocks, dropout
            ).to(self.device)
            from backend.infrastructure.recommenders.sequence_training import train_sequence

            self.epoch_history, measurements = train_sequence(
                self.network,
                self.sequences,
                eligible,
                n_items=len(self.items),
                epochs=epochs,
                batch_size=batch_size,
                negatives=negatives,
                learning_rate=learning_rate,
                probe_batches=probe_batches,
                progress=progress,
            )
            self.execution.update(measurements)
        self.network.eval()
        self.execution["parameter_count"] = sum(p.numel() for p in self.network.parameters())
        self.execution["epochs"] = self.epoch_history
        if use_gpu:
            self.execution["training_peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
        for path, digest in zip(events_path, events_sha256, strict=True):
            if file_sha256(path) != digest:
                raise ValueError("Training events changed during sequence training")
        if checkpoint_path is not None:
            from backend.infrastructure.recommenders.sequence_checkpoint import save_checkpoint

            save_checkpoint(checkpoint_path, self.network, self.items.tolist(), seed)
        self.training_seconds = time.perf_counter() - started
        self.cache_hit = False

    def _scores(self, user_ids):
        import torch

        if self.execution["mode"] == "resource_probe":
            raise ValueError("Resource probe cannot be used for recommendation evaluation")
        with torch.no_grad():
            ids = torch.as_tensor(user_ids, device=self.device, dtype=torch.long)
            return self.network.score(self.sequences[ids, 1:])

    def gpu_score_batch(self, user_ids):
        if self.device.type != "cuda":
            raise ValueError("CUDA sequence model required")
        import cupy as cp

        return cp.from_dlpack(self._scores(user_ids))

    def rank(self, user, history, task, top_k=20):
        if task not in {"new", "repeat"}:
            raise ValueError("task must be new or repeat")
        scores = self._scores([self.users[user]])[0].cpu().numpy()
        candidates = np.flatnonzero(
            [(item in history) == (task == "repeat") for item in self.items]
        )
        order = np.lexsort((candidates, -self.plays[candidates], -scores[candidates]))[:top_k]
        return self.items[candidates[order]].tolist()
