"""初期選択と聴取履歴を混同しない、アプリ向けプロフィール変換。"""

from dataclasses import dataclass, field

from src.domain.recommendation.contracts import RecommendationRequest, validate_id, validate_ids


@dataclass(frozen=True)
class ProfileRequest(RecommendationRequest):
    input_kind: str = field(default="initial_profile", init=False)

    def __post_init__(self):
        super().__post_init__()
        if self.task != "new":
            raise ValueError("initial selections are not listening history for repeat evaluation")


@dataclass(frozen=True)
class InitialProfile:
    user_id: str
    selected_item_ids: tuple[str, ...] = ()
    selected_artist_ids: tuple[str, ...] = ()

    def __post_init__(self):
        validate_id(self.user_id)
        validate_ids(self.selected_item_ids)
        validate_ids(self.selected_artist_ids)

    def to_request(self, request_id, candidate_ids, *, artist_tracks=None, top_k=20):
        """new専用。artist_tracksは確認済みのアプリ内artist ID→曲ID対応表。"""
        artist_tracks = artist_tracks or {}
        seeds = set(self.selected_item_ids)
        for artist in self.selected_artist_ids:
            values = artist_tracks.get(artist, ())
            validate_ids(values)
            seeds.update(values)
        return ProfileRequest(
            request_id, self.user_id, tuple(sorted(seeds)), candidate_ids, top_k, "new"
        )
