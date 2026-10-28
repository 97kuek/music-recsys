"""Run a lazy search plan within its count/time budget and checkpoint each result."""

import time
from collections.abc import Callable

from backend.application.ports.search import SearchBackend
from backend.domain.search import SearchProgress


class Search:
    def __init__(self, backend: SearchBackend, *, clock: Callable[[], float] = time.perf_counter):
        self._backend = backend
        self._clock = clock

    def smoke(self, config: object) -> dict:
        return self._run(config, smoke=True)

    def run(self, config: object) -> dict:
        return self._run(config, smoke=False)

    def _run(self, config, *, smoke):
        with self._backend.open(config, smoke=smoke) as session:
            if not smoke and not session.smoke_passed:
                raise ValueError("Smoke Testを先に実行してください")
            session.prepare()
            progress = SearchProgress(
                1 if smoke else session.planned,
                1 if smoke else session.max_trials,
                None if smoke else session.time_budget,
                smoke=smoke,
            )
            started = self._clock()
            session.checkpoint(progress.snapshot())
            plans = iter(session.plans())
            try:
                while progress.can_start(self._clock() - started):
                    trial = next(plans)
                    outcome = session.execute(trial)
                    progress.record(outcome, self._clock() - started)
                    session.checkpoint(progress.snapshot())
            except Exception:
                progress.abort(self._clock() - started)
                session.checkpoint(progress.snapshot())
                raise
            progress.finish(self._clock() - started)
            session.checkpoint(progress.snapshot())
            return session.result()
