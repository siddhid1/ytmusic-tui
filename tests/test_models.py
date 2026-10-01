"""Tests for track models and result parsing."""

from ytm_tui.models import format_duration
from ytm_tui.ytm import _parse_duration, _to_track


def test_format_duration():
    assert format_duration(0) == "0:00"
    assert format_duration(65) == "1:05"
    assert format_duration(3600) == "1:00:00"
    assert format_duration(3661) == "1:01:01"
    assert format_duration(None) == "--:--"
    assert format_duration(-5) == "--:--"


def test_parse_duration():
    assert _parse_duration("3:45") == 225
    assert _parse_duration("1:02:03") == 3723
    assert _parse_duration("0:07") == 7
    assert _parse_duration("") is None
    assert _parse_duration(None) is None
    assert _parse_duration("abc") is None
    assert _parse_duration("1:2:3:4") is None  # too many parts treated as invalid


def test_to_track_from_search_item():
    item = {
        "videoId": "abc123",
        "title": "Get Lucky",
        "artists": [{"name": "Daft Punk"}, {"name": "Pharrell Williams"}],
        "album": {"name": "Random Access Memories", "id": "x"},
        "duration": "6:10",
    }
    track = _to_track(item)
    assert track is not None
    assert track.video_id == "abc123"
    assert track.title == "Get Lucky"
    assert track.artists == ("Daft Punk", "Pharrell Williams")
    assert track.album == "Random Access Memories"
    assert track.duration == 370
    assert track.url == "https://music.youtube.com/watch?v=abc123"
    assert track.display_title == "Get Lucky — Daft Punk, Pharrell Williams"


def test_to_track_missing_video_id():
    assert _to_track({"title": "no id"}) is None


def test_to_track_minimal_fields():
    track = _to_track({"videoId": "z", "title": None})
    assert track is not None
    assert track.title == "Unknown title"
    assert track.artists == ()
    assert track.album is None
    assert track.duration is None
    assert track.artist_str == "Unknown artist"
    assert track.duration_str == "--:--"
