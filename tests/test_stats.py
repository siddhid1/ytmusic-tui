"""Local play-stats storage, aggregation, and the app flush hook."""

from __future__ import annotations

import json
import os

from ytm_tui import stats
from ytm_tui.models import Track


def _track(video_id: str = "v1", artists: tuple[str, ...] = ("A", "B")) -> Track:
    return Track(video_id=video_id, title="Song", artists=artists)


def test_record_and_load_roundtrip(tmp_path):
    path = tmp_path / "local_stats.json"
    event = stats.record(_track(), 42.5, now=1000.0, path=path)
    assert event is not None
    events = stats.load_events(path)
    assert len(events) == 1
    assert events[0].video_id == "v1"
    assert events[0].title == "Song"
    assert events[0].artists == ("A", "B")
    assert events[0].seconds == 42.5
    assert events[0].ts == 1000.0
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_short_listen_not_recorded(tmp_path):
    path = tmp_path / "local_stats.json"
    assert stats.record(_track(), stats.MIN_SECONDS - 0.1, path=path) is None
    assert not path.exists()


def test_cap_keeps_recent_events(tmp_path):
    path = tmp_path / "local_stats.json"
    old = [
        {"video_id": f"old{i}", "title": "t", "artists": ["a"], "seconds": 10.0, "ts": 0.0}
        for i in range(stats.MAX_EVENTS)
    ]
    path.write_text(json.dumps({"events": old}), encoding="utf-8")
    stats.record(_track(video_id="new"), 10.0, now=1.0, path=path)
    events = stats.load_events(path)
    assert len(events) == stats.MAX_EVENTS
    assert events[0].video_id == "old1"  # oldest event dropped
    assert events[-1].video_id == "new"


def test_corrupt_file_yields_empty(tmp_path):
    path = tmp_path / "local_stats.json"
    path.write_text("{not json", encoding="utf-8")
    assert stats.load_events(path) == []


def test_artist_stats_multi_artist_and_threshold():
    events = [
        stats.PlayEvent("a", "A", ("X", "Y"), 60.0, 0.0),  # play credit for both
        stats.PlayEvent("b", "B", ("X",), 29.0, 0.0),  # seconds only — below 30s
        stats.PlayEvent("c", "C", (), 45.0, 0.0),  # no artists credited
    ]
    table = stats.artist_stats(events)
    by_name = {s.name: s for s in table}
    assert by_name["X"].seconds == 89.0
    assert by_name["X"].plays == 1
    assert by_name["Y"].seconds == 60.0
    assert by_name["Y"].plays == 1
    assert by_name["Unknown artist"].seconds == 45.0
    assert table[0].name == "X"  # sorted by seconds, most listened first


def test_app_flush_records_event(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from ytm_tui.app import YTMusicTUI

    app = YTMusicTUI()
    app._loaded_track = _track(video_id="v9", artists=("Solo",))
    app.position = 42.0
    app._flush_play_stats()
    events = stats.load_events()
    assert len(events) == 1
    assert events[0].video_id == "v9"
    assert events[0].artists == ("Solo",)
    assert events[0].seconds == 42.0
    assert app._loaded_track is None
    app._flush_play_stats()  # nothing loaded → no-op
    assert len(stats.load_events()) == 1
