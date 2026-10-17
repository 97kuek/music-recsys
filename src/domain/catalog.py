"""既存item_idを維持する、外部楽曲参照と再生可否の契約。"""

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from src.domain.datasets import TrackRecord
from src.domain.recommendation.contracts import validate_id, validate_ids


def normalize(value):
    # バージョン表記・括弧・live/remixを削除しない。照合の証拠には使わない。
    return " ".join(unicodedata.normalize("NFKC", value).split())


def aware_time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")


@dataclass(frozen=True)
class TrackRef:
    provider: str
    source_id: str

    def __post_init__(self):
        validate_id(self.provider)
        validate_id(self.source_id)


@dataclass(frozen=True)
class TrackSource:
    ref: TrackRef
    revision: str
    title: str | None = None
    artists: tuple[str, ...] = ()
    isrc: str | None = None

    def __post_init__(self):
        if not isinstance(self.ref, TrackRef):
            raise ValueError("ref must be TrackRef")
        validate_id(self.revision)
        validate_ids(self.artists)
        for value in (self.title, self.isrc):
            if value is not None:
                validate_id(value)


@dataclass(frozen=True)
class CatalogTrack:
    item_id: str
    sources: tuple[TrackSource, ...]

    def __post_init__(self):
        validate_id(self.item_id)
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ValueError("sources must be a nonempty tuple")
        if not all(isinstance(source, TrackSource) for source in self.sources):
            raise ValueError("sources must contain TrackSource")
        if len({source.ref for source in self.sources}) != len(self.sources):
            raise ValueError("duplicate source reference")

    @classmethod
    def music4all(cls, record: TrackRecord, release: str) -> "CatalogTrack":
        # 学習IDとタグ特徴量は変換しない。未知のタイトル・再生権は補完しない。
        return cls(
            record.source_id, (TrackSource(TrackRef("music4all", record.source_id), release),)
        )


class TrackCatalog:
    """確認済みの対応のみ登録する。ISRC・タイトルによる自動マージはしない。"""

    def __init__(self, tracks: tuple[CatalogTrack, ...]):
        self._tracks: dict[str, CatalogTrack] = {}
        self._refs: dict[TrackRef, str] = {}
        self.links: tuple[TrackLink, ...] = ()
        for track in tracks:
            if track.item_id in self._tracks:
                raise ValueError("duplicate item_id")
            self._tracks[track.item_id] = track
            for source in track.sources:
                if source.ref in self._refs:
                    raise ValueError("source reference belongs to multiple tracks")
                self._refs[source.ref] = track.item_id

    def with_link(self, link: "TrackLink") -> "TrackCatalog":
        if link.relation != "same_recording":
            raise ValueError("only confirmed same-recording links may be merged")
        original = self.get(link.item_id)
        updated = CatalogTrack(original.item_id, original.sources + (link.source,))
        result = TrackCatalog(
            tuple(updated if t.item_id == link.item_id else t for t in self._tracks.values())
        )
        result.links = self.links + (link,)
        return result

    def presentation(self, item_id, context, availability, *, at):
        aware_time(at)
        track = self.get(item_id)
        sources = [source for source in track.sources if source.ref == context.ref]
        if not sources:
            raise ValueError("playback source is not linked to this track")
        status = "unknown"
        if availability is not None:
            if availability.context != context:
                raise ValueError("playback context mismatch")
            if availability.checked_at <= at < availability.expires_at:
                status = availability.status
        return TrackPresentation(item_id, sources[0].title is not None, status, context.mode)

    def get(self, item_id: str) -> CatalogTrack:
        return self._tracks[item_id]

    def resolve(self, ref: TrackRef) -> str | None:
        return self._refs.get(ref)


@dataclass(frozen=True)
class PlaybackContext:
    ref: TrackRef
    market: str
    listener_id: str  # アプリ内の仮名ID。認証トークンを格納しない。
    mode: Literal["link", "preview", "full"]

    def __post_init__(self):
        if not isinstance(self.ref, TrackRef):
            raise ValueError("ref must be TrackRef")
        validate_id(self.market)
        validate_id(self.listener_id)
        if self.mode not in ("link", "preview", "full"):
            raise ValueError("unknown playback mode")


@dataclass(frozen=True)
class PlaybackAvailability:
    context: PlaybackContext
    status: Literal["unknown", "available", "unavailable"]
    checked_at: datetime
    expires_at: datetime

    def __post_init__(self):
        if not isinstance(self.context, PlaybackContext):
            raise ValueError("context must be PlaybackContext")
        aware_time(self.checked_at)
        aware_time(self.expires_at)
        if self.status not in ("unknown", "available", "unavailable"):
            raise ValueError("unknown availability status")
        if self.expires_at <= self.checked_at:
            raise ValueError("availability needs a positive validity interval")

    def permits(self, context: PlaybackContext, at: datetime) -> bool:
        aware_time(at)
        return (
            self.context == context
            and self.status == "available"
            and self.checked_at <= at < self.expires_at
        )


class CatalogAdapter(Protocol):
    """外部接続用の境界。バッチ取り込みのDatasetAdapterとは別の責務。"""

    def lookup(self, ref: TrackRef) -> TrackSource | None: ...

    def availability(self, context: PlaybackContext) -> PlaybackAvailability: ...


@dataclass(frozen=True)
class TrackLink:
    """同一録音と確認された対応のみ、既存IDへ追加できる。"""

    item_id: str
    source: TrackSource
    relation: Literal["same_recording", "different_version", "unconfirmed"]
    evidence_ref: str

    def __post_init__(self):
        validate_id(self.item_id)
        validate_id(self.evidence_ref)
        if not isinstance(self.source, TrackSource):
            raise ValueError("source must be TrackSource")
        if self.relation not in ("same_recording", "different_version", "unconfirmed"):
            raise ValueError("unknown recording relation")


@dataclass(frozen=True)
class TrackPresentation:
    item_id: str
    displayable: bool
    playback_status: Literal["unknown", "available", "unavailable"]
    mode: Literal["link", "preview", "full"]
