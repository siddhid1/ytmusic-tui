"""History adapter maps ytmusicapi shelf items onto HistoryEntry (no network)."""

from __future__ import annotations

from ytm_tui import ytm
from ytm_tui.models import HistoryEntry, Track
from ytm_tui.ytm import YTMusicClient

RAW = [
    {
        "videoId": "h1",
        "title": "Instant Crush",
        "artists": [{"name": "Daft Punk"}],
        "album": {"name": "Random Access Memories"},
        "duration": "3:57",
        "duration_seconds": 237,
        "played": "Today",
        "feedbackToken": "tok1",
    },
    {"title": "no video id", "played": "Today"},  # skipped
    {
        "videoId": "h2",
        "title": "Nightcall",
        "artists": [{"name": "Kavinsky"}],
        "duration": "4:18",
        "played": "Yesterday",
    },
]


class FakeYT:
    def get_history(self):
        return RAW


def _client(monkeypatch) -> YTMusicClient:
    monkeypatch.setattr(ytm, "_build_yt", FakeYT)
    return YTMusicClient()


def test_history_mapping(monkeypatch):
    entries = _client(monkeypatch).history()
    assert all(isinstance(e, HistoryEntry) for e in entries)
    assert [e.track.video_id for e in entries] == ["h1", "h2"]
    assert all(isinstance(e.track, Track) for e in entries)
    assert entries[0].played == "Today"
    assert entries[0].feedback_token == "tok1"
    assert entries[1].played == "Yesterday"
    assert entries[1].feedback_token == ""
    assert entries[0].track.album == "Random Access Memories"
    assert entries[0].track.duration == 237
    assert entries[0].track.duration_str == "3:57"


def test_history_error_propagates(monkeypatch):
    class Boom:
        def get_history(self):
            raise RuntimeError("auth required")

    monkeypatch.setattr(ytm, "_build_yt", Boom)
    try:
        YTMusicClient().history()
    except RuntimeError as exc:
        assert "auth required" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
