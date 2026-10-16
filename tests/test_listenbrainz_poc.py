from music_recsys.listenbrainz_poc import audit, jamendo_id


def test_urls_and_versions_are_not_merged():
    assert jamendo_id("https://www.jamendo.com/track/123/song") == "123"
    assert jamendo_id("https://jamendo.com.evil/track/123") is None
    assert jamendo_id("https://jamendo.com/album/123") is None
    assert jamendo_id("file://jamendo.com/track/123") is None
    catalog = [{"source_id": "123", "title": "Song", "artist": "Artist"}]
    listens = [{"track_metadata": {"track_name": "Song (Live)", "artist_name": "Artist"}}]
    rows, result = audit(listens, catalog)
    assert rows[0]["status"] == "unmatched"
    assert result["candidate_rate"] == 0
    assert result["verified_recording_match_rate"] is None


def test_name_and_submitted_url_are_candidates_not_proof():
    catalog = [{"source_id": "123", "title": "Song", "artist": "Artist"}]
    listens = [
        {
            "user_name": "private-name",
            "track_metadata": {
                "track_name": "Song",
                "artist_name": "Artist",
                "additional_info": info,
            },
        }
        for info in ({}, {"origin_url": "https://jamendo.com/track/123"})
    ]
    rows, result = audit(listens, catalog)
    assert [r["status"] for r in rows] == ["ambiguous_name_candidate", "source_id_candidate"]
    assert "private-name" not in repr(rows) + repr(result)
    assert result["verified_recording_match_rate"] is None
    assert result["false_match_rate"] is None
    assert result["candidate_rate"] == 1


def test_empty_sample_has_no_measured_rate():
    _, result = audit([], [])
    assert result["candidate_rate"] is None
