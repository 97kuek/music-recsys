"""Order experiment steps and publish success only after every required step passes."""

import math

from src.application.ports.experiments import (
    EvaluationBackend,
    EvaluationOptions,
    SensitivityBackend,
    StudyBackend,
)
from src.domain.experiments import Progress


class Evaluation:
    def __init__(self, backend: EvaluationBackend):
        self._backend = backend

    def run(self, source: object, options: EvaluationOptions = EvaluationOptions()):
        with self._backend.open(source, options) as session:
            session.train()
            result = session.score()
            session.render(result)
            session.verify()
            session.export(result)
            session.publish(result)
            return session.output


class FactorStudy:
    def __init__(self, backend: StudyBackend):
        self._backend = backend

    def run(self, source: object, *, minimum_free_gb=5):
        if not math.isfinite(minimum_free_gb) or minimum_free_gb < 0:
            raise ValueError("minimum_free_gb must be finite and non-negative")
        with self._backend.open(source) as session:
            if session.complete:
                for name in session.conditions:
                    session.verify(name)
                return session.output
            state = Progress(session.conditions)
            finished = []
            try:
                for name in session.conditions:
                    session.check_space(minimum_free_gb)
                    state.start(name)
                    session.checkpoint(state.snapshot())
                    if not session.completed(name):
                        session.record(f"START {name} ({state.completed + 1}/{state.planned})")
                        session.execute(name)
                    result = session.verify(name)
                    finished.append((name, result))
                    state.complete(session.evidence(name, result))
                    session.record(f"DONE {name}; completed={state.completed}/{state.planned}")
                    session.checkpoint(state.snapshot())
                figures = session.summarize(finished)
                session.record(
                    "SUCCEEDED: all conditions completed; no model selected; Test not opened"
                )
                state.succeed(figures)
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                state.fail(message)
                session.record(message)
                raise
            finally:
                session.checkpoint(state.snapshot())
            return session.output


class Sensitivity:
    """Verify controls and completed trials before publishing a Validation comparison."""

    def __init__(self, backend: SensitivityBackend):
        self._backend = backend

    def run(self, source: object, *, minimum_free_gb=5):
        if not math.isfinite(minimum_free_gb) or minimum_free_gb < 0:
            raise ValueError("minimum_free_gb must be finite and non-negative")
        with self._backend.open(source) as session:
            if session.complete:
                for name in session.conditions:
                    session.verify(name)
                return session.output
            for batch in session.batches:
                if not session.batch_completed(batch):
                    session.check_space(minimum_free_gb)
                    session.execute_batch(batch)
            finished = []
            definitions = None
            for name in session.conditions:
                if not session.completed(name):
                    session.check_space(minimum_free_gb)
                    session.execute(name)
                result, evidence = session.verify(name)
                comparable = {
                    key: value
                    for key, value in result["definitions"].items()
                    if key not in {"ranking", "latency"}
                }
                if definitions is not None and comparable != definitions:
                    raise ValueError("comparison evaluation definitions differ")
                definitions = comparable
                finished.append((name, result, evidence))
            session.publish(finished)
            return session.output
