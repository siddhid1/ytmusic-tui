"""Tests for multi-select mark logic and title-cell rendering."""

from __future__ import annotations

from rich.text import Text

from ytm_tui.app import YTMusicTUI, _title_cell
from ytm_tui.models import Track


def _track(video_id: str, title: str = "T") -> Track:
    return Track(video_id=video_id, title=title, artists=("Artist",))


class TestTitleCell:
    def test_plain_string_when_inactive(self):
        assert _title_cell("Song", playing=False, marked=False) == "Song"

    def test_playing_prefix(self):
        cell = _title_cell("Song", playing=True, marked=False)
        assert isinstance(cell, Text)
        assert cell.plain == "▶ Song"
        assert cell.style == "bold green"

    def test_marked_prefix(self):
        cell = _title_cell("Song", playing=False, marked=True)
        assert isinstance(cell, Text)
        assert cell.plain == "● Song"
        assert cell.style == "bold yellow"

    def test_playing_and_marked(self):
        cell = _title_cell("Song", playing=True, marked=True)
        assert isinstance(cell, Text)
        assert cell.plain == "▶● Song"
        assert cell.style == "bold green"


class TestMarkedTracks:
    def setup_method(self):
        self.app = YTMusicTUI()
        self.app._results = [_track("aaa", "A"), _track("bbb", "B"), _track("ccc", "C")]

    def test_empty_marks(self):
        self.app._cursor_track = lambda: None  # type: ignore[method-assign]
        assert self.app._marked_tracks() == []
        assert self.app._targets() == []

    def test_display_order_results_first(self):
        self.app.queue.enqueue(_track("ddd", "D"))
        self.app._marked = {"ddd", "ccc", "aaa"}
        assert [t.video_id for t in self.app._marked_tracks()] == ["aaa", "ccc", "ddd"]

    def test_dedupe_between_results_and_queue(self):
        self.app.queue.enqueue(_track("aaa", "A"))
        self.app._marked = {"aaa"}
        assert len(self.app._marked_tracks()) == 1

    def test_targets_prefers_marks_over_cursor(self):
        self.app._marked = {"bbb"}
        self.app._cursor_track = lambda: _track("aaa", "A")  # type: ignore[method-assign]
        assert [t.video_id for t in self.app._targets()] == ["bbb"]

    def test_targets_falls_back_to_cursor(self):
        self.app._cursor_track = lambda: _track("aaa", "A")  # type: ignore[method-assign]
        assert [t.video_id for t in self.app._targets()] == ["aaa"]

    def test_marks_of_vanished_tracks_are_dropped_from_targets(self):
        self.app._marked = {"zzz"}  # not in results or queue anymore
        assert self.app._marked_tracks() == []

    def test_playlist_title_lookup(self):
        from ytm_tui.models import Playlist

        self.app._playlists = [Playlist(playlist_id="PL1", title="Road Trip", count="7")]
        assert self.app._playlist_title("PL1") == "Road Trip"
        assert self.app._playlist_title("nope") == "nope"
