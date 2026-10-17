from datetime import UTC, datetime

from src.catalog import CatalogTrack, PlaybackContext, TrackRef, TrackSource, normalize
from src.catalog_poc import MetadataCatalogAdapter
from src.recommenders.tag_seed import TagSeedRecommender
from src.serving import FixedStrategy, ModelSpec, RecommendationService
from src.types import RecommendationRequest


def test_metadata_is_not_playback_evidence():
    ref = TrackRef("jamendo", "123")
    adapter = MetadataCatalogAdapter((CatalogTrack("mtg:123", (TrackSource(ref, "sha"),)),))
    context = PlaybackContext(ref, "JP", "poc", "full")
    assert adapter.lookup(ref).ref == ref
    assert adapter.lookup(TrackRef("jamendo", "456")) is None
    assert not adapter.availability(context).permits(context, datetime.now(UTC))
    assert adapter.availability(context).status == "unknown"


def test_versions_not_erased_by_normalization():
    assert normalize("  Song　(Live)  ") == "Song (Live)"
    assert normalize("Song (Live)") != normalize("Song")


def test_seed_recommendation_uses_contract_without_fake_listens():
    model = TagSeedRecommender({"a": {"rock": 1}, "b": {"rock": 2}, "c": {"jazz": 1}})
    spec = ModelSpec("v1", "content-tags", "{}", "input", "catalog", "test", 42)
    service = RecommendationService(((spec, model),))
    request = RecommendationRequest("r", "new-user", ("a",), ("a", "b", "c"), 5)
    assert service.recommend(request, FixedStrategy("v1")).response.item_ids == ("b",)
    empty = RecommendationRequest("e", "new-user", (), ("a", "b", "c"), 5)
    assert service.recommend(empty, FixedStrategy("v1")).response.item_ids == ()
