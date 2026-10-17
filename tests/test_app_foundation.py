import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from src.application.recommendation import RecommendationService
from src.domain.catalog import (
    CatalogTrack,
    PlaybackAvailability,
    PlaybackContext,
    TrackCatalog,
    TrackRef,
    TrackSource,
)
from src.domain.datasets import TrackRecord
from src.domain.evaluation.dataset import Interaction
from src.domain.recommendation.contracts import RecommendationRequest, RecommendationResponse
from src.domain.recommendation.models import FixedStrategy, ModelSpec
from src.domain.study.events import InteractionEvent, SessionLog, StudySession
from src.infrastructure.recommenders import create_recommender

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def spec(algorithm="popularity", params="{}"):
    return ModelSpec("model-v1", algorithm, params, "train-sha", "catalog-sha", "git-sha", 42)


def execution():
    model = create_recommender("popularity", [Interaction("u", "a"), Interaction("u", "b")])
    request = RecommendationRequest("r", "u", ("a",), ("a", "b"), 2)
    return RecommendationService(((spec(), model),)).recommend(request, FixedStrategy("model-v1"))


def session(**kwargs):
    return StudySession(
        "s",
        "u",
        "study",
        "protocol-v1",
        "condition-a",
        "AB",
        1,
        "consent-v1",
        NOW,
        NOW + timedelta(days=1),
        **kwargs,
    )


def test_catalog_preserves_ids_and_never_merges_isrc_automatically():
    legacy = CatalogTrack.music4all(TrackRecord("old-id", None), "release-v2")
    a = CatalogTrack("app:a", (TrackSource(TrackRef("one", "1"), "v1", isrc="same"),))
    b = CatalogTrack("app:b", (TrackSource(TrackRef("two", "1"), "v1", isrc="same"),))
    catalog = TrackCatalog((legacy, a, b))
    assert catalog.resolve(TrackRef("music4all", "old-id")) == "old-id"
    assert catalog.get("old-id").sources[0].title is None
    assert catalog.resolve(TrackRef("one", "1")) == "app:a"
    assert catalog.resolve(TrackRef("two", "1")) == "app:b"
    assert catalog.resolve(TrackRef("unknown", "1")) is None
    with pytest.raises(ValueError, match="multiple tracks"):
        TrackCatalog((a, replace(a, item_id="other")))
    with pytest.raises(ValueError, match="duplicate item_id"):
        TrackCatalog((a, a))


def test_playback_availability_is_scoped_and_expires():
    context = PlaybackContext(TrackRef("one", "1"), "JP", "u", "preview")
    availability = PlaybackAvailability(context, "available", NOW, NOW + timedelta(minutes=1))
    assert availability.permits(context, NOW)
    assert not availability.permits(context, NOW + timedelta(minutes=1))
    assert not availability.permits(context, NOW - timedelta(seconds=1))
    for other in (
        replace(context, market="US"),
        replace(context, listener_id="v"),
        replace(context, mode="full"),
    ):
        assert not availability.permits(other, NOW)
    assert not replace(availability, status="unknown").permits(context, NOW)
    with pytest.raises(ValueError, match="timezone"):
        availability.permits(context, datetime(2026, 1, 1))


@pytest.mark.parametrize("algorithm", ["popularity", "itemcf", "als", "bpr"])
@pytest.mark.parametrize("task", ["new", "repeat"])
@pytest.mark.parametrize("user", ["u", "cold"])
def test_service_preserves_existing_model_results(algorithm, task, user):
    params = {"factors": 3, "iterations": 2} if algorithm in ("als", "bpr") else {}
    model = create_recommender(
        algorithm,
        [Interaction(u, i) for u, items in (("u", "ab"), ("v", "bc")) for i in items],
        parameters=params,
        seed=42,
    )
    request = RecommendationRequest(
        "r", user, ("a",) if user == "u" else (), ("a", "b", "c", "z"), 3, task
    )
    service = RecommendationService(((spec(algorithm, json.dumps(params)), model),))
    result = service.recommend(request, FixedStrategy("model-v1"))
    assert result.response == model.recommend(request)
    assert result.model.parameters == params
    assert result.inference_seconds >= 0
    assert result.selection_seconds >= 0


@pytest.mark.parametrize(
    "response",
    [
        RecommendationResponse("wrong", ("b",)),
        RecommendationResponse("r", ("b", "b")),
        RecommendationResponse("r", ("a",)),
        RecommendationResponse("r", ("unknown",)),
    ],
)
def test_service_rejects_invalid_model_output(response):
    class BadModel:
        def recommend(self, request):
            return response

    with pytest.raises(ValueError):
        RecommendationService(((spec(), BadModel()),)).recommend(
            execution().request, FixedStrategy("model-v1")
        )


def test_model_metadata_is_immutable_and_selection_is_registered():
    metadata = spec(params='{"factors":32}')
    metadata.parameters["factors"] = 256
    assert metadata.parameters == {"factors": 32}
    with pytest.raises(ValueError):
        spec(params='{"factors":NaN}')
    model = create_recommender("popularity", [])
    with pytest.raises(ValueError, match="unique"):
        RecommendationService(((metadata, model), (metadata, model)))
    with pytest.raises(ValueError, match="unregistered"):
        RecommendationService(((metadata, model),)).recommend(
            execution().request, FixedStrategy("missing")
        )


def test_log_minimizes_data_and_preserves_nullable_usage_and_provenance():
    log = SessionLog(session())
    log.record(execution(), now=NOW, request_text="private text")
    payload = log.export(now=NOW)
    row = payload["requests"][0]
    assert "request_text" not in row
    assert "history" not in row and "user_id" not in row and "candidate_ids" not in row
    assert row["response"]["usage"] is None
    assert row["model"]["training_ref"] == "train-sha"
    assert row["model"]["parameters"] == {}
    json.dumps(payload)
    payload["requests"].clear()
    assert len(log.export(now=NOW)["requests"]) == 1
    opted_in = SessionLog(session(allow_request_text=True))
    opted_in.record(execution(), now=NOW, request_text="explicitly consented")
    assert opted_in.export(now=NOW)["requests"][0]["request_text"] == "explicitly consented"


def test_events_link_to_exposure_and_retries_are_idempotent():
    log = SessionLog(session())
    log.record(execution(), now=NOW)
    event = InteractionEvent("e", "s", "r", "impression", NOW, "b")
    log.append(event, now=NOW)
    log.append(event, now=NOW)
    assert len(log.export(now=NOW)["events"]) == 1
    for invalid in (
        replace(event, session_id="other"),
        replace(event, request_id="other"),
        replace(event, item_id="a"),
        replace(event, occurred_at=NOW + timedelta(seconds=1)),
        replace(event, kind="play"),
    ):
        with pytest.raises(ValueError):
            log.append(invalid, now=NOW)
    with pytest.raises(ValueError, match="already recorded"):
        log.record(execution(), now=NOW)
    with pytest.raises(ValueError, match="participant"):
        SessionLog(replace(session(), participant_id="someone-else")).record(execution(), now=NOW)


def test_rating_scale_withdrawal_and_retention():
    with pytest.raises(ValueError, match="outside scale"):
        InteractionEvent(
            "e",
            "s",
            "r",
            "rating",
            NOW,
            rating=6,
            instrument_id="satisfaction-v1",
            scale_min=1,
            scale_max=5,
        )
    for expire in (False, True):
        log = SessionLog(session())
        log.record(execution(), now=NOW)
        if not expire:
            log.withdraw()
        at = session().retain_until if expire else NOW
        with pytest.raises(ValueError, match="withdrawn, expired"):
            log.export(now=at)
        assert not log._requests and not log._events
        with pytest.raises(ValueError):
            log.record(execution(), now=NOW)


def test_subjective_rating_and_failed_recommendation_are_distinct():
    log = SessionLog(session())
    log.record(execution(), now=NOW)
    rating = InteractionEvent(
        "rating-1",
        "s",
        "r",
        "rating",
        NOW,
        rating=4,
        instrument_id="overall-satisfaction-v1",
        scale_min=1,
        scale_max=5,
    )
    log.append(rating, now=NOW)
    assert log.export(now=NOW)["events"][0]["rating"] == 4
    failed = replace(execution(), response=RecommendationResponse("r", (), "error", "unavailable"))
    failures = SessionLog(session())
    failures.record(failed, now=NOW)
    assert failures.export(now=NOW)["requests"][0]["response"]["status"] == "error"
    with pytest.raises(ValueError, match="failed recommendation"):
        failures.append(rating, now=NOW)


def test_log_rejects_model_mismatch_and_unmeasured_latency():
    result = execution()
    for invalid in (
        replace(result, selection=replace(result.selection, model_version_id="another")),
        replace(result, inference_seconds=float("nan")),
        replace(result, selection_seconds=-1),
    ):
        with pytest.raises(ValueError):
            SessionLog(session()).record(invalid, now=NOW)
