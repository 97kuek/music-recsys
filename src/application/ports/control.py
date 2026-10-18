"""Ports for optional decision providers and privacy-limited evaluation traces."""

from typing import Protocol

from src.domain.recommendation.control import DecisionAnswer, DecisionInput


class DecisionProvider(Protocol):
    external: bool

    def choose(self, evidence: DecisionInput, *, timeout_seconds: float) -> DecisionAnswer: ...


class ControlTrace(Protocol):
    external: bool

    def record(self, *, trace_id: str, metadata: dict) -> None: ...
