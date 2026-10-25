"""期限なしと期限切れを区別し、音源の利用可否を検証する。"""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.catalog import PlaybackContext, TrackRef
from src.infrastructure.media.playback import AudioAsset, LocalPlayback, parse_expiry


@pytest.mark.parametrize("offset, available", [(None, True), (1, True), (0, False), (-1, False)])
def test_audio_expiry_and_explicit_no_expiry(tmp_path, offset, available):
    now = datetime(2030, 1, 1, tzinfo=UTC)
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"test-only")
    expiry = None if offset is None else now + timedelta(seconds=offset)
    playback = LocalPlayback({"track": AudioAsset(path, "researcher-confirmation", expiry, False)})
    context = PlaybackContext(TrackRef("music4all", "track"), "local", "test", "preview")
    status = playback.availability(context, now)
    assert status.status == ("available" if available else "unavailable")
    # PlaybackAvailability自身の短い確認期限は、音源の許諾期限とは別
    assert status.expires_at == now + timedelta(seconds=30)
    if available:
        assert playback.file(context, now) == path
    else:
        with pytest.raises(ValueError):
            playback.file(context, now)
    path.unlink()
    assert playback.availability(context, now).status == "unavailable"


@pytest.mark.parametrize("value", ["", "unknown", "2030-01-01", 1, False])
def test_expiry_requires_explicit_null_or_aware_timestamp(value):
    with pytest.raises(ValueError):
        parse_expiry(value)


def test_explicit_null_round_trip_and_naive_asset_rejected(tmp_path):
    assert parse_expiry(None) is None
    assert parse_expiry("2030-01-01T09:00:00+09:00") == datetime(2030, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone"):
        AudioAsset(tmp_path / "clip.mp3", "confirmed", datetime(2030, 1, 1), False)
