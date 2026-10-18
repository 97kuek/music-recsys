"""配信カタログと再生可否の表示。セッション状態を持たない。"""

from src.application.ports.study import Clock, Playback
from src.domain.catalog import CatalogTrack, PlaybackContext, TrackCatalog, TrackRef, TrackSource


class StudyCatalog:
    def __init__(self, mode: str, records: list[dict], playback: Playback, clock: Clock):
        self.mode, self.playback, self.clock = mode, playback, clock
        self.tracks = {row["item_id"]: row for row in records}
        if len(self.tracks) != len(records):
            raise ValueError("duplicate catalog item")
        provider = "sample" if self.mode == "demo" else self.mode
        self.catalog = TrackCatalog(
            tuple(
                CatalogTrack(
                    row["item_id"],
                    (
                        TrackSource(
                            TrackRef(provider, row.get("source_id", row["item_id"])),
                            "app-v1",
                            row.get("title"),
                            tuple(row.get("artists", [])),
                        ),
                    ),
                )
                for row in records
            )
        )
        self.artists = {}
        for row in records:
            for artist in row.get("artists", []):
                self.artists.setdefault(artist, []).append(row["item_id"])
        self.artists = {key: tuple(value) for key, value in self.artists.items()}

    def context(self, item, participant):
        return PlaybackContext(
            self.catalog.get(item).sources[0].ref, "local", participant, "preview"
        )

    def track(self, item, participant="catalog"):
        source = self.tracks[item]
        context = self.context(item, participant)
        availability = self.playback.availability(context, self.clock())
        presentation = self.catalog.presentation(item, context, availability, at=self.clock())
        return {
            "item_id": item,
            "title": source.get("title") or f"楽曲名未取得 · {item}",
            "artists": source.get("artists") or ["アーティスト未取得"],
            "artwork": source.get("artwork"),
            "provider": self.mode,
            "license_url": source.get("license_url"),
            "sample": self.mode == "demo",
            "playback_status": presentation.playback_status,
        }
