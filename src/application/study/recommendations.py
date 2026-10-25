"""比較条件と初期入力から推薦要求を作り、実行結果をセッションへ記録する。"""

from copy import deepcopy
from dataclasses import asdict, replace
from uuid import uuid4

from src.application.ports.recommendation import ServedRecommender
from src.application.ports.study import Clock
from src.application.recommendation import RecommendationService
from src.application.study.catalog import StudyCatalog
from src.application.study.sessions import SessionService
from src.domain.recommendation.contracts import RecommendationRequest
from src.domain.recommendation.models import ComparisonStrategy, ModelSpec
from src.domain.study.plan import StudyPlan


class RecommendationActions:
    def __init__(
        self,
        models: dict[str, tuple[ModelSpec, ServedRecommender]],
        sessions: SessionService,
        catalog: StudyCatalog,
        study: StudyPlan | None,
        clock: Clock,
    ):
        self.models, self.sessions, self.catalog = models, sessions, catalog
        self.study, self.clock = study, clock
        self.service = RecommendationService(tuple(models.values()))
        self.candidates = {
            version: tuple(item for item in model.index if item in catalog.tracks)
            for version, (_, model) in models.items()
        }
        self.study_candidates = None
        if self.study:
            if self.study.design == "exploratory_strategy" or any(
                c.controller in {"llm_single", "reactive_agent"} for c in self.study.conditions
            ):
                raise ValueError(
                    "LLM/Agent execution is not implemented; protocol is analysis-only"
                )
            versions = [condition.version for condition in self.study.conditions]
            if any(version not in self.models for version in versions):
                raise ValueError("study model artifacts are not ready")
            shared = set.intersection(*(set(self.candidates[v]) for v in versions))
            self.study_candidates = tuple(
                sorted(
                    item
                    for item in shared
                    if self.catalog.tracks[item].get("title")
                    and self.catalog.tracks[item].get("artists")
                    and self.catalog.playback.availability(
                        self.catalog.context(item, "preflight"), self.clock()
                    ).status
                    == "available"
                )
            )
            if len(self.study_candidates) < self.study.top_k:
                raise ValueError("study requires enough verified metadata and playable tracks")

    def recommend(self, token, version, goal, top_k, text):
        session = self.sessions.get(token)
        if session.finished:
            raise ValueError("終了済みです。新しいセッションを開始してください")
        if version not in self.models:
            raise ValueError("指定モデルは未準備です")
        if len(session.recommendations) >= 100:
            raise ValueError("1セッションの推薦回数上限です")
        spec, model = self.models[version]
        candidates = self.candidates[version]
        if self.study:
            candidates = self.study_candidates
            self.sessions.freeze_comparison_input(session)
        request_id = str(uuid4())
        if goal == "familiar" and session.heard and not self.study:
            request = RecommendationRequest(
                request_id,
                session.profile.user_id,
                tuple(sorted(session.heard)),
                candidates,
                top_k,
                "repeat",
            )
        else:
            request = session.profile.to_request(
                request_id, candidates, artist_tracks=self.catalog.artists, top_k=top_k
            )
            if session.heard and not self.study:
                request = replace(
                    request, history=tuple(sorted(set(request.history) | session.heard))
                )
        if self.study and len(set(candidates) - set(request.history)) < top_k:
            raise ValueError("比較に必要な未聴候補が不足しています。初期選択を減らしてください")
        execution = self.service.recommend(request, ComparisonStrategy(version))
        if self.study:
            condition = self.study.conditions[session.order[session.period]]
            if condition.version != version:
                raise ValueError("割り当てられた実験条件とモデルが一致しません")
            if condition.required_route and execution.inference_route != condition.required_route:
                raise ValueError("実験条件に必要な入力が不足しています。実施者に確認してください")
        session.log.record(
            execution,
            now=self.clock(),
            request_text=text or None,
            condition_id=self.study.conditions[session.order[session.period]].condition_id
            if self.study
            else "normal",
        )
        session.recommendations[request_id] = execution.response.item_ids
        session.goals[request_id] = {
            "goal": goal,
            "effective_task": request.task,
            "text_interpreted": False,
            "input_kind": getattr(request, "input_kind", "listening_history"),
            "initial_profile": asdict(session.profile),
            "heard_item_ids": sorted(session.heard),
            "top_k": top_k,
            "period": session.period + 1,
            "list_number": 1,
            "adjustment_index": 0,
            "parent_request_id": None,
            "session_context": deepcopy(session.context),
            "condition": self.study.conditions[session.order[session.period]].model_dump()
            if self.study
            else None,
        }
        reply = {
            "request_id": request_id,
            "tracks": [
                self.catalog.track(item, session.profile.user_id)
                for item in execution.response.item_ids
            ],
            "model_version": spec.version_id,
            "algorithm": spec.algorithm,
            "route": execution.inference_route,
            "latency_ms": execution.inference_seconds * 1000,
            "goal": goal,
            "effective_task": request.task,
            "notice": (
                "好きな曲・アーティストを選び直してください。共通する特徴のある候補がありません"
                if not execution.response.item_ids and spec.algorithm == "content-tags"
                else "まだ再生履歴がないため、初期プロフィールから候補を提案します"
                if goal == "familiar" and not session.heard
                else "おまかせは暫定的に未聴候補を提示します。既聴・未聴混合の総合推薦は未対応です"
                if goal == "auto"
                else None
            ),
            "mode": self.catalog.mode,
        }
        session.replies[session.period] = reply
        session.goals[request_id]["playability"] = {
            track["item_id"]: track["playback_status"] for track in reply["tracks"]
        }
        self.sessions.save(token, session)
        return reply

    def assigned_recommendation(self, token, version, goal, top_k, text):
        current = self.sessions.get(token)
        if current.finished:
            raise ValueError("終了済みです")
        if self.study:
            if current.phase != "listen":
                raise ValueError("試聴画面で実行してください")
            if current.period in current.replies:
                return current.replies[current.period]
            condition = self.study.conditions[current.order[current.period]]
            reply = self.recommend(token, condition.version, "discover", self.study.top_k, "")
        else:
            reply = self.recommend(token, version, goal, top_k, text)
        return reply
