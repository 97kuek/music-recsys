"""認証済みAPIの背後に置く、明示許諾ローカル音源用Adapter。"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.domain.catalog import PlaybackAvailability


@dataclass(frozen=True)
class AudioAsset:
    path: Path
    license_ref: str
    expires_at: datetime
    sample: bool


class LocalPlayback:
    def __init__(self, assets: dict[str, AudioAsset]):
        self.assets = assets

    def availability(self, context, now):
        from datetime import timedelta

        asset = self.assets.get(context.ref.source_id)
        status = "unknown"
        if asset is not None:
            status = (
                "available"
                if asset.license_ref and asset.expires_at > now and asset.path.is_file()
                else "unavailable"
            )
        return PlaybackAvailability(context, status, now, now + timedelta(seconds=30))

    def file(self, context, now):
        if not self.availability(context, now).permits(context, now):
            raise ValueError("音源が未接続、期限切れ、または再生不可です")
        return self.assets[context.ref.source_id].path

    def resolve(self, context, now):
        self.file(context, now)
        return None
