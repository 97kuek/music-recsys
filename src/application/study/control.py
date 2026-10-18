"""Explicit researcher HTTP control using injected orchestration and decision adapters."""

from dataclasses import asdict

from src.application.strategy_control import StrategyControl
from src.application.study.sessions import token_key
from src.domain.recommendation.control import frozen_input_digest


class ResearchControlActions:
    def __init__(
        self,
        recommendations,
        sessions,
        *,
        targets,
        feedback_choices,
        budget,
        runner_factory,
        config_key,
        provider=None,
        trace=None,
    ):
        self.recommendations, self.sessions = recommendations, sessions
        self.targets, self.feedback_choices, self.budget = targets, feedback_choices, budget
        self.runner_factory, self.config_key = runner_factory, config_key
        self.provider, self.trace = provider, trace
        self.controllers = {}
        if sessions.study:
            raise ValueError("research control cannot be mixed with participant study assignment")
        versions = [version for version, _ in targets]
        if not versions or any(v not in recommendations.models for v in versions):
            raise ValueError("control targets must be registered models")
        shared = set.intersection(*(set(recommendations.candidates[v]) for v in versions))
        self.candidates = tuple(sorted(shared))
        if not shared:
            raise ValueError("control targets need shared catalog candidates")

    def expire(self):
        active = {s.log.session.session_id for s in self.sessions.active.values()}
        for key in list(self.controllers):
            if key[0] not in active:
                del self.controllers[key]

    def run(
        self,
        token,
        *,
        request_id,
        mode,
        top_k,
        fixed_target,
        explicit_adjustment,
        parent_request_id,
        feedback,
        permission,
    ):
        current = self.sessions.get(token)
        self.expire()
        if not current.persist or current.finished:
            raise ValueError("control requires an active session with explicit log storage")
        if not 2 <= top_k <= 20:
            raise ValueError("control top_k must be between 2 and 20")
        if len(current.recommendations) >= 100:
            raise ValueError("session recommendation limit reached")
        if mode != "fixed" and self.provider is None:
            raise ValueError("an explicit decision model and provider must be configured")
        request = current.profile.to_request(
            request_id, self.candidates, artist_tracks=self.sessions.catalog.artists, top_k=top_k
        )
        session_id = current.log.session.session_id
        key = (session_id, mode)
        saved = current.control_states.get(mode)
        command = {
            "parent": parent_request_id,
            "adjustment": explicit_adjustment,
            "choices": list(feedback.choices),
            "permission": asdict(permission),
            "has_text": bool(feedback.text),
        }
        if saved:
            if saved["config_key"] != self.config_key or saved["fixed_target"] != fixed_target:
                raise ValueError("control configuration changed within the session")
            if saved["input_digest"] != frozen_input_digest(request):
                raise ValueError("control inputs changed within the session")
            if key not in self.controllers:
                raise ValueError(
                    "control cannot resume after restart; start a new research session"
                )
            if saved.get("in_flight"):
                raise ValueError("an interrupted control request cannot be repeated")
            if request_id in saved["responses"]:
                if saved["commands"][request_id] != command or feedback.text:
                    raise ValueError("request_id was already used for another control command")
                return saved["responses"][request_id]
            if len(saved["attempts"]) >= 100:
                raise ValueError("research control attempt limit reached")
        if key not in self.controllers:
            control = StrategyControl(
                self.recommendations.service,
                session_id=session_id,
                condition_id=f"research-control:{mode}",
                mode=mode,
                targets=self.targets,
                feedback_choices=self.feedback_choices,
                budget=self.budget,
                provider=self.provider,
                fixed_target=fixed_target,
                trace=self.trace,
            )
            runner = self.runner_factory(control)
            self.controllers[key] = (control, runner)
            saved = {
                "config_key": self.config_key,
                "fixed_target": fixed_target,
                "input_digest": frozen_input_digest(request),
                "responses": {},
                "attempts": [],
                "calls": 0,
                "commands": {},
            }
            current.control_states[mode] = saved
        control, runner = self.controllers[key]
        # Persist a marker before any external invocation. Restart/uncertain failures
        # fail closed, so a lost process cannot silently reset a paid-call budget.
        saved["in_flight"] = request_id
        try:
            self.sessions.save(token, current)
        except Exception:
            self.sessions.active.pop(token_key(token), None)
            self.controllers.pop(key, None)
            raise
        try:
            step = runner.run(
                request,
                explicit_adjustment=explicit_adjustment,
                parent_request_id=parent_request_id,
                feedback=feedback,
                permission=permission,
                log=current.log,
                now=self.sessions.clock(),
            )
            reply = {
                "status": step.status,
                "reason": step.reason,
                "audit": step.audit(),
                "recommendation": None,
            }
            if step.status == "ok":
                execution = step.execution
                tracks = [
                    self.sessions.catalog.track(item, current.profile.user_id)
                    for item in execution.response.item_ids
                ]
                current.recommendations[request_id] = execution.response.item_ids
                current.goals[request_id] = {
                    "goal": "discover",
                    "effective_task": request.task,
                    "input_kind": request.input_kind,
                    "initial_profile": asdict(current.profile),
                    "heard_item_ids": sorted(current.heard),
                    "top_k": top_k,
                    "period": current.period + 1,
                    "condition": None,
                    "list_number": step.adjustment_index + 1,
                    "adjustment_index": step.adjustment_index,
                    "parent_request_id": parent_request_id,
                    "playability": {t["item_id"]: t["playback_status"] for t in tracks},
                }
                recommendation = {
                    "request_id": request_id,
                    "tracks": tracks,
                    "model_version": execution.model.version_id,
                    "algorithm": execution.model.algorithm,
                    "route": execution.inference_route,
                    "latency_ms": execution.inference_seconds * 1000,
                    "notice": None,
                }
                current.replies[current.period] = recommendation
                reply["recommendation"] = recommendation
            saved["attempts"].append(step.audit())
            saved["calls"] = control.calls
            saved["responses"][request_id] = reply
            saved["commands"][request_id] = command
        except ValueError:
            # Invalid parent/inputs never invoke a decision provider.
            saved["in_flight"] = None
            self.sessions.save(token, current)
            raise
        except Exception:
            # Keep the durable marker after an uncertain invocation.
            self.controllers.pop(key, None)
            raise
        saved["in_flight"] = None
        try:
            self.sessions.save(token, current)
        except Exception:
            self.controllers.pop(key, None)
            raise
        return reply
