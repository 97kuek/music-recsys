"""配信ユースケースの組立。セッション・推薦・再生の業務処理は各サービスが担う。"""

from threading import RLock

from src.application.ports.recommendation import ServedRecommender
from src.application.ports.study import Clock, Playback, SessionRepository
from src.application.study import clock as system_clock
from src.application.study.catalog import StudyCatalog
from src.application.study.playback import PlaybackService
from src.application.study.recommendations import RecommendationActions
from src.application.study.sessions import SessionService
from src.domain.recommendation.models import ModelSpec
from src.domain.study.plan import StudyPlan


class Runtime:
    def __init__(
        self,
        *,
        config: dict,
        config_sha256: str,
        models: dict[str, tuple[ModelSpec, ServedRecommender]],
        records: list[dict],
        playback: Playback,
        store: SessionRepository,
        study: StudyPlan | None = None,
        scope=None,
        clock: Clock | None = None,
    ):
        if config.get("mode") not in ("demo", "music4all"):
            raise ValueError("mode must explicitly be demo or music4all")
        clock = clock or (lambda: system_clock.utc_now())
        self.config = config
        self.lock = RLock()
        self.catalog = StudyCatalog(config["mode"], records, playback, clock)
        self.sessions = SessionService(
            mode=config["mode"],
            context={
                "config_sha256": config_sha256,
                "plan_sha256": study.digest if study else None,
                "scope": scope,
            },
            store=store,
            catalog=self.catalog,
            study=study,
            clock=clock,
        )
        self.recommendations = RecommendationActions(
            models, self.sessions, self.catalog, study, clock
        )
        self.playback = PlaybackService(self.sessions, self.catalog, clock)
