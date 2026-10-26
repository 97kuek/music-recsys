"""Order experiment steps and publish success only after every required step passes."""

import math

from src.application.ports.experiments import EvaluationBackend, EvaluationOptions, StudyBackend


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
            state = dict(
                format="music-recsys-factor-study-v1",
                status="running",
                phase="validation",
                planned=len(session.conditions),
                completed=0,
                conditions=[],
                figures=[],
            )
            finished = []
            try:
                for name in session.conditions:
                    session.check_space(minimum_free_gb)
                    state["current"] = name
                    session.checkpoint(state)
                    if not session.completed(name):
                        session.record(
                            f"START {name} ({state['completed'] + 1}/{state['planned']})"
                        )
                        session.execute(name)
                    result = session.verify(name)
                    finished.append((name, result))
                    state["conditions"].append(session.evidence(name, result))
                    state["completed"] = len(finished)
                    session.record(f"DONE {name}; completed={len(finished)}/{state['planned']}")
                    session.checkpoint(state)
                state["figures"] = session.summarize(finished)
                state.update(status="succeeded", current=None)
                session.record(
                    "SUCCEEDED: all conditions completed; no model selected; Test not opened"
                )
            except Exception as error:
                state.update(status="failed", error=f"{type(error).__name__}: {error}")
                session.record(state["error"])
                raise
            finally:
                session.checkpoint(state)
            return session.output
