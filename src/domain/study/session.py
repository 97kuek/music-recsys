"""参加者セッションの状態。永続化やHTTPを含めない。"""

from dataclasses import dataclass, field

from src.domain.recommendation.profiles import InitialProfile
from src.domain.study.events import SessionLog


@dataclass
class Session:
    log: SessionLog
    persist: bool
    profile: InitialProfile
    registered: bool = False
    heard: set = field(default_factory=set)
    recommendations: dict = field(default_factory=dict)
    playbacks: dict = field(default_factory=dict)
    playback_bindings: dict = field(default_factory=dict)
    preview_bindings: dict = field(default_factory=dict)
    goals: dict = field(default_factory=dict)
    finished: bool = False
    termination_reason: str | None = None
    order: list = field(default_factory=list)
    period: int = 0
    phase: str = "profile"
    funnel: list = field(default_factory=list)
    replies: dict = field(default_factory=dict)
    long_term_consent: bool = False
    profile_snapshot: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    assignment: dict = field(default_factory=dict)
