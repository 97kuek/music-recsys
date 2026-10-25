"""Run candidate preparation before evaluation through an injected study adapter."""

from typing import Protocol

from src.domain.recommendation.pipeline import HybridCondition


class HybridStudyPort(Protocol):
    def prepare(self, source_id: str) -> dict: ...
    def evaluate(self, condition: HybridCondition, candidates: dict[str, dict]) -> dict: ...
    def summarize(self, results: list[dict]) -> str: ...


def run_study(
    source_ids: tuple[str, ...], conditions: tuple[HybridCondition, ...], port: HybridStudyPort
) -> str:
    """Failures propagate; no failed source or user is silently discarded."""
    if len(source_ids) != len(set(source_ids)) or len({c.name for c in conditions}) != len(
        conditions
    ):
        raise ValueError("Source IDs and condition names must be unique")
    if any(set(c.sources) - set(source_ids) for c in conditions):
        raise ValueError("Condition refers to an undeclared candidate source")
    candidates = {name: port.prepare(name) for name in source_ids}
    results = [
        port.evaluate(condition, {name: candidates[name] for name in condition.sources})
        for condition in conditions
    ]
    return port.summarize(results)
