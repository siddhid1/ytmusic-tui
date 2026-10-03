"""Textual application shell: search, queue, playback controls, player bar."""

from __future__ import annotations

import os
import time
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Header, Input, Static, Tab, TabbedContent, TabPane, Tabs

from . import art
from .models import HistoryEntry, Playlist, Track
from .mpv_client import MpvClient, MpvError
from .queue import QueueModel
from .widgets import (
    HelpScreen,
    LoginScreen,
    NowPlayingPanel,
    PlayerBar,
    PlaylistPicker,
    hints_for,
)
from .ytm import YTMusicClient, art_url

SEARCH_DEBOUNCE = 0.4
SEEK_STEP = 5.0
VOLUME_STEP = 5
G_PREFIX_TIMEOUT = 1.0
ART_PANEL_MIN_WIDTH = 110
LIBRARY_SECTIONS = ("playlists", "albums", "artists")
LIBRARY_STALE_SECONDS = 300.0

# Main tab id → table that receives cursor/marks while that tab is active.
_TAB_TABLE = {
    "results": "#results-table",
    "queue": "#queue-table",
    "library": "#library-table",
    "history": "#history-table",
    "profile": "#profile-table",
}
_SECTION_TABS = {
    "lib-playlists": "playlists",
    "lib-albums": "albums",
    "lib-artists": "artists",
}


class TrackTable(DataTable, inherit_bindings=False):
    """DataTable without left/right bindings so arrows can seek from the app."""

    BINDINGS = [
        Binding("enter", "select_cursor", "Play", show=False),
        Binding("up", "cursor_up", "Cursor up", show=False),
        Binding("down", "cursor_down", "Cursor down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("home", "scroll_home", "Home", show=False),
        Binding("end", "scroll_end", "End", show=False),
        Binding("ctrl+home", "scroll_top", show=False),
        Binding("ctrl+end", "scroll_bottom", show=False),
    ]


def _placeholder(table: TrackTable, message: str, cells: int = 4) -> None:
    """Add a dimmed single-cell status/placeholder row."""
    row: list[Any] = [""] * cells
    row[1 if cells > 1 else 0] = Text(message, style="dim")
    table.add_row(*row)


def _title_cell(title: str, playing: bool, marked: bool) -> Text | str:
    """Row title cell: plain string normally, marked/playing prefixes when active."""
    if playing and marked:
        return Text(f"▶● {title}", style="bold green")
    if playing:
        return Text(f"▶ {title}", style="bold green")
    if marked:
        return Text(f"● {title}", style="bold yellow")
    return title


class YTMusicTUI(App):
    TITLE = "yt-tui-player"
    SUB_TITLE = "YouTube Music in your terminal"

    CSS = """
    Screen {
        background: $surface;
    }
    #search {
        dock: top;
        width: 100%;
    }
    #tabs {
        height: 1fr;
    }
    TabbedContent ContentSwitcher {
        height: 1fr;
    }
    TabPane {
        height: 100%;
        padding: 0;
    }
    DataTable {
        height: 100%;
    }
    #player-bar {
        dock: bottom;
        height: 3;
        border-top: solid $accent;
        background: $panel;
        padding: 0 1;
    }
    #help-box {
        width: 74;
        max-width: 94%;
        height: auto;
        max-height: 85%;
        border: tall $accent;
        background: $panel;
        padding: 1 2;
    }
    #help-content {
        width: 100%;
    }
    #login-box {
        width: 74;
        max-width: 94%;
        height: auto;
        max-height: 85%;
        border: tall $accent;
        background: $panel;
        padding: 1 2;
    }
    #login-status {
        margin-top: 1;
    }
    #headers-paste {
        height: 9;
        margin-top: 1;
    }
    #login-hints {
        margin-top: 1;
    }
    #picker-box {
        width: 64;
        max-width: 94%;
        height: auto;
        max-height: 85%;
        border: tall $accent;
        background: $panel;
        padding: 1 2;
    }
    #picker-list {
        height: auto;
        max-height: 20;
    }
    #art-panel {
        dock: right;
        width: 19;
        background: $panel;
        padding: 0 1;
        border-left: solid $accent;
    }
    #art-image {
        width: 100%;
    }
    #art-meta {
        width: 100%;
        margin-top: 1;
    }
    #lib-tabs {
        height: auto;
    }
    #library-table, #history-table, #profile-table {
        height: 1fr;
    }
    #profile-summary {
        height: auto;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search", show=False),
        Binding("r", "show_results", "Results", show=False),
        Binding("q", "show_queue", "Queue", show=False),
        Binding("1", "show_tab('results')", "Results", show=False),
        Binding("2", "show_tab('queue')", "Queue", show=False),
        Binding("3", "show_tab('library')", "Library", show=False),
        Binding("4", "show_tab('history')", "History", show=False),
        Binding("5", "show_tab('profile')", "Profile", show=False),
        Binding("space", "toggle_pause", "Play/Pause", show=False),
        Binding("n", "next_track", "Next", show=False),
        Binding("p", "prev_track", "Prev", show=False),
        Binding("left", "seek_backward", "Seek -5s", show=False),
        Binding("right", "seek_forward", "Seek +5s", show=False),
        Binding("h", "seek_backward", show=False),
        Binding("l", "seek_forward", show=False),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("g", "g_prefix", show=False),
        Binding("G", "scroll_last", show=False),
        Binding("ctrl+d", "half_page_down", show=False),
        Binding("ctrl+u", "half_page_up", show=False),
        Binding("ctrl+f", "page_down", show=False),
        Binding("ctrl+b", "page_up", show=False),
        Binding("+", "volume_up", "Volume +", show=False),
        Binding("=", "volume_up", show=False),
        Binding("-", "volume_down", "Volume -", show=False),
        Binding("a", "enqueue", "Add to queue", show=False),
        Binding("d", "remove", "Remove", show=False),
        Binding("t", "cycle_theme", "Theme", show=False),
        Binding("escape", "go_back", "Back", show=False),
        Binding("?", "show_help", "Help", show=False),
        Binding("ctrl+l", "login", "Login", show=False),
        Binding("s", "toggle_mark", "Mark", show=False),
        Binding("S", "clear_marks", "Clear marks", show=False),
        Binding("A", "add_to_playlist", "Add to playlist", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.queue = QueueModel()
        self.ytm = YTMusicClient()
        # Extra mpv args (e.g. YT_TUI_MPV_EXTRA="--ao=null" for tests).
        extra = [arg for arg in os.environ.get("YT_TUI_MPV_EXTRA", "").split() if arg]
        self.mpv = MpvClient(extra_args=extra)
        self.mpv.set_event_handler(self._mpv_event_from_thread)
        self._results: list[Track] = []
        self._search_token = 0
        self._search_timer: Any = None
        self._pending_account: dict[str, Any] | None = None
        self.playing = False
        self.paused = False
        self.position: float | None = None
        self.duration: float | None = None
        self.volume = 100
        self._error_retries = 0
        self._g_pending = False
        self._g_timer: Any = None
        self._marked: set[str] = set()
        self._playlists: list[Playlist] | None = None
        self._pending_add: list[Track] | None = None
        self._art_token = 0
        # Library tab: cached section lists, per-section load state, drill-down.
        self._lib_lists: dict[str, list[Any]] = {s: [] for s in LIBRARY_SECTIONS}
        self._lib_state: dict[str, str] = {s: "unloaded" for s in LIBRARY_SECTIONS}
        self._lib_errors: dict[str, str] = {s: "" for s in LIBRARY_SECTIONS}
        self._lib_fetched_at: float | None = None
        self._lib_token = 0
        self._lib_section = "playlists"
        self._lib_drill: Playlist | None = None
        self._lib_drill_loading = False
        self._lib_drill_error = ""
        self._lib_tracks: list[Track] = []
        # History tab: read-only list of recent plays.
        self._history: list[HistoryEntry] = []
        self._history_state = "unloaded"  # unloaded | loading | ready | error
        self._history_error = ""
        self._history_fetched_at: float | None = None
        self._history_token = 0

    # -- composition --------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(placeholder="Search YouTube Music… (press / )", id="search")
        with TabbedContent(id="tabs"):
            with TabPane("Results", id="results"):
                yield TrackTable(
                    id="results-table",
                    cursor_type="row",
                    zebra_stripes=True,
                )
            with TabPane("Queue (0)", id="queue"):
                yield TrackTable(
                    id="queue-table",
                    cursor_type="row",
                    zebra_stripes=True,
                )
            with TabPane("Library", id="library"):
                yield Tabs(
                    Tab("Playlists", id="lib-playlists"),
                    Tab("Albums", id="lib-albums"),
                    Tab("Artists", id="lib-artists"),
                    id="lib-tabs",
                )
                yield TrackTable(
                    id="library-table",
                    cursor_type="row",
                    zebra_stripes=True,
                )
            with TabPane("History", id="history"):
                yield TrackTable(
                    id="history-table",
                    cursor_type="row",
                    zebra_stripes=True,
                )
            with TabPane("Profile", id="profile"):
                yield Static("Listening stats appear here", id="profile-summary")
                yield TrackTable(
                    id="profile-table",
                    cursor_type="row",
                    zebra_stripes=True,
                )
        yield NowPlayingPanel(id="art-panel")
        yield PlayerBar(id="player-bar")

    def on_mount(self) -> None:
        results = self.query_one("#results-table", TrackTable)
        results.add_column("Title", width=42)
        results.add_column("Artist", width=38)
        results.add_column("Album", width=22)
        results.add_column("Dur", width=6)
        _placeholder(results, "Press / to search, Enter to play")
        queue = self.query_one("#queue-table", TrackTable)
        queue.add_column("#", width=4)
        queue.add_column("Title", width=50)
        queue.add_column("Artist", width=46)
        queue.add_column("Dur", width=6)
        library = self.query_one("#library-table", TrackTable)
        library.add_column("Title", width=42)
        library.add_column("Artist", width=38)
        library.add_column("Album", width=22)
        library.add_column("Dur", width=6)
        _placeholder(library, "Press 3 to open your library")
        history = self.query_one("#history-table", TrackTable)
        history.add_column("When", width=10)
        history.add_column("Title", width=40)
        history.add_column("Artist", width=36)
        history.add_column("Album", width=18)
        history.add_column("Dur", width=6)
        _placeholder(history, "History loads after sign-in", cells=5)
        profile = self.query_one("#profile-table", TrackTable)
        profile.add_column("Artist", width=44)
        profile.add_column("Recent", width=16)
        profile.add_column("This app", width=16)
        profile.add_column("Plays", width=8)
        _placeholder(profile, "Sign in (ctrl+l), then play some songs")
        self.query_one("#search", Input).focus()
        theme = os.environ.get("YT_TUI_THEME")
        if theme and theme in self.available_themes:
            self.theme = theme
        # Mode chip + hints follow focus (Screen.focused is reactive).
        self.watch(self.screen, "focused", self._sync_bar, init=False)
        self._sync_bar()
        self._update_art(None)
        self._start_mpv()

    def on_resize(self, event: events.Resize) -> None:
        # Hide the cover column on narrow terminals so tables keep their width.
        panel = self.query_one("#art-panel", NowPlayingPanel)
        panel.display = event.size.width >= ART_PANEL_MIN_WIDTH

    def shutdown(self) -> None:
        try:
            self.mpv.close()
        except Exception:
            pass

    # -- mpv lifecycle ------------------------------------------------------
    @work(thread=True, exclusive=True, group="mpv-start")
    def _start_mpv(self) -> None:
        try:
            self.mpv.start()
        except MpvError as exc:
            self.call_from_thread(
                self.notify,
                f"mpv failed to start: {exc}",
                title="Player error",
                severity="error",
                timeout=10,
                markup=False,
            )

    def _mpv_event_from_thread(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return  # app is shutting down; call_from_thread would orphan a coroutine
        self.call_from_thread(self._on_mpv_event, event)

    def _on_mpv_event(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        if name == "end-file":
            reason = event.get("reason")
            if reason == "eof":
                self._advance()
            elif reason == "error":
                failed = self.queue.current
                title = failed.title if failed else "?"
                # Transient stream errors (e.g. HTTP 403) are common; retry once.
                if failed is not None and self._error_retries < 1:
                    self._error_retries += 1
                    self.notify(
                        f"Stream failed, retrying: {title}",
                        title="Playback warning",
                        severity="warning",
                        markup=False,
                    )
                    self._load_current()
                    return
                self._error_retries = 0
                self.notify(
                    f"Failed to play: {title}",
                    title="Playback error",
                    severity="error",
                    markup=False,
                )
                self._advance()
            # stop/redirect/quit: we replaced the file ourselves or are exiting
        elif name == "file-loaded":
            self._error_retries = 0
        elif name == "property-change":
            prop = event.get("name")
            data = event.get("data")
            if prop == "time-pos" and data is not None:
                self.position = float(data)
                self._sync_bar()
            elif prop == "pause" and data is not None:
                self.paused = bool(data)
                self._sync_bar()
            elif prop == "duration" and data is not None:
                self.duration = float(data)
                self._sync_bar()
            elif prop == "volume" and data is not None:
                self.volume = int(data)
                self._sync_bar()
        elif name == "mpv-connection-lost":
            self.playing = False
            self.notify(
                "Lost connection to mpv — playback stopped",
                title="Player error",
                severity="error",
            )
            self._sync_bar()

    # -- playback -----------------------------------------------------------
    @property
    def _mode(self) -> str:
        return "insert" if isinstance(self.focused, Input) else "normal"

    @property
    def _active_tab(self) -> str:
        return self.query_one("#tabs", TabbedContent).active

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self) -> None:
        self._sync_bar()
        if self._active_tab == "library":
            self._ensure_library()
        elif self._active_tab == "history":
            self._ensure_history()

    def _sync_bar(self) -> None:
        mode = self._mode
        bar = self.query_one("#player-bar", PlayerBar)
        bar.sync(
            track=self.queue.current,
            position=self.position,
            duration=self.duration,
            playing=self.playing,
            paused=self.paused,
            volume=self.volume,
            queue_index=(self.queue.cursor + 1) if self.queue.current else 0,
            queue_length=len(self.queue),
            mode=mode,
            marked=len(self._marked),
            authed=self.ytm.authed,
            hints=hints_for(
                mode,
                self._active_tab,
                library_playlist=self._active_tab == "library" and self._lib_drill is not None,
            ),
        )

    def _load_current(self) -> None:
        self._rebuild_results()
        track = self.queue.current
        if track is None:
            self.playing = False
            self._update_art(None)
            self._sync_bar()
            return
        if not self.mpv.is_running:
            self.notify("mpv is not running", severity="error", title="Player error")
            return
        try:
            self.mpv.load(track.url)
        except MpvError as exc:
            self.notify(str(exc), severity="error", title="Player error", markup=False)
            return
        self.playing = True
        self.paused = False
        self.position = 0.0
        self.duration = float(track.duration) if track.duration else None
        self._update_art(track)
        self._sync_bar()

    # -- album art -----------------------------------------------------------
    def _update_art(self, track: Track | None) -> None:
        """Refresh the now-playing panel and kick off a cover fetch."""
        panel = self.query_one("#art-panel", NowPlayingPanel)
        panel.show_track(track)
        self._art_token += 1
        token = self._art_token
        if track is None or not track.thumbnail:
            panel.show_art(None)
            return
        self._fetch_art(art_url(track.thumbnail), token)

    @work(thread=True, exclusive=True, group="art", exit_on_error=False)
    def _fetch_art(self, url: str, token: int) -> None:
        data = art.fetch(url)
        rendered = art.render_halfblock(data) if data is not None else None
        self.call_from_thread(self._art_done, token, rendered)

    def _art_done(self, token: int, rendered: Text | None) -> None:
        if token != self._art_token:
            return  # a newer track replaced this fetch
        self.query_one("#art-panel", NowPlayingPanel).show_art(rendered)

    def _advance(self) -> None:
        if self.queue.next() is None:
            self.playing = False
            self.paused = False
            self.position = None
            self.duration = None
            self.notify("Queue finished", timeout=4)
            self._rebuild_queue()
            self._rebuild_results()
            self._sync_bar()
            return
        self._rebuild_queue()
        self._load_current()

    # -- search -------------------------------------------------------------
    @on(Input.Changed, "#search")
    def _on_search_changed(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(SEARCH_DEBOUNCE, self._run_search)

    @on(Input.Submitted, "#search")
    def _on_search_submitted(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None
        self.query_one("#results-table", TrackTable).focus()
        self._run_search()

    def _run_search(self) -> None:
        query = self.query_one("#search", Input).value.strip()
        table = self.query_one("#results-table", TrackTable)
        if not query:
            self._results = []
            table.clear()
            _placeholder(table, "Press / to search, Enter to play")
            return
        self._search_token += 1
        token = self._search_token
        self._results = []
        self._marked.clear()
        table.clear()
        _placeholder(table, f"Searching “{query}”…")
        self._search(query, token)

    @work(thread=True, exclusive=True, group="search", exit_on_error=False)
    def _search(self, query: str, token: int) -> None:
        try:
            tracks = self.ytm.search_songs(query)
        except Exception as exc:  # network / API errors surface as toasts
            self.call_from_thread(self._search_failed, token, str(exc))
            return
        self.call_from_thread(self._search_done, token, tracks)

    def _search_done(self, token: int, tracks: list[Track]) -> None:
        if token != self._search_token:
            return  # stale response from an older query
        self._results = tracks
        table = self.query_one("#results-table", TrackTable)
        if not tracks:
            table.clear()
            _placeholder(table, "No results")
            return
        self._rebuild_results()
        table.move_cursor(row=0)

    def _add_track_rows(self, table: TrackTable, tracks: list[Track]) -> None:
        """Append track rows, marking playing/marked songs (▶ / ● prefixes)."""
        current = self.queue.current
        for track in tracks:
            playing = current is not None and track.video_id == current.video_id
            marked = track.video_id in self._marked
            table.add_row(
                _title_cell(track.title, playing, marked),
                Text(track.artist_str, style="green") if playing else track.artist_str,
                track.album or "",
                track.duration_str,
            )

    def _rebuild_results(self) -> None:
        """Re-render result rows, marking the playing track with ▶."""
        if not self._results:
            return
        table = self.query_one("#results-table", TrackTable)
        cursor = table.cursor_row
        table.clear()
        self._add_track_rows(table, self._results)
        if table.row_count:
            table.move_cursor(row=min(max(cursor, 0), table.row_count - 1))

    def _search_failed(self, token: int, error: str) -> None:
        if token != self._search_token:
            return
        table = self.query_one("#results-table", TrackTable)
        table.clear()
        _placeholder(table, f"Search failed: {error}")

    # -- queue table --------------------------------------------------------
    def _rebuild_queue(self) -> None:
        table = self.query_one("#queue-table", TrackTable)
        table.clear()
        for index, track in enumerate(self.queue.items, start=1):
            is_current = self.queue.current is not None and index - 1 == self.queue.cursor
            marked = track.video_id in self._marked
            if is_current:
                table.add_row(
                    Text("▶", style="bold green"),
                    _title_cell(track.title, playing=True, marked=marked),
                    Text(track.artist_str, style="green"),
                    track.duration_str,
                )
            else:
                table.add_row(
                    str(index),
                    _title_cell(track.title, playing=False, marked=marked),
                    track.artist_str,
                    track.duration_str,
                )
        if self.queue.current is not None:
            table.move_cursor(row=self.queue.cursor)
        try:
            tab = self.query_one("#tabs", TabbedContent).get_tab("queue")
            tab.label = f"Queue ({len(self.queue)})"
        except Exception:
            pass
        self._sync_bar()

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id
        row = event.cursor_row
        if table_id == "results-table":
            if 0 <= row < len(self._results):
                self._play_now(self._results[row])
        elif table_id == "queue-table":
            if 0 <= row < len(self.queue):
                self.queue.select(row)
                self._rebuild_queue()
                self._load_current()
        elif table_id == "library-table":
            self._library_row_selected(row)
        elif table_id == "history-table":
            if 0 <= row < len(self._history):
                self._play_now(self._history[row].track)

    def _play_now(self, track: Track) -> None:
        for index, queued in enumerate(self.queue.items):
            if queued.video_id == track.video_id:
                self.queue.select(index)
                break
        else:
            self.queue.enqueue(track)
        self._rebuild_queue()
        self._load_current()

    # -- library tab ---------------------------------------------------------
    @on(Tabs.TabActivated, "#lib-tabs")
    def _on_library_section(self, event: Tabs.TabActivated) -> None:
        section = _SECTION_TABS.get(event.tab.id or "", "playlists")
        self._lib_section = section
        if self._active_tab != "library":
            return  # mount-time activation; fetch waits for the first visit
        self._close_library_drill()
        self._ensure_library()
        table = self.query_one("#library-table", TrackTable)
        if table.row_count:
            table.move_cursor(row=0)

    def _ensure_library(self) -> None:
        """Render the active section; (re)fetch when unloaded or stale."""
        if (
            self._lib_fetched_at is not None
            and time.monotonic() - self._lib_fetched_at > LIBRARY_STALE_SECONDS
        ):
            self._reset_library()
        if not self.ytm.authed:
            self._render_library()
            return
        todo = [s for s in LIBRARY_SECTIONS if self._lib_state[s] == "unloaded"]
        if todo:
            for section in todo:
                self._lib_state[section] = "loading"
            self._lib_token += 1
            self._fetch_library(todo, self._lib_token)
        self._render_library()

    @work(thread=True, exclusive=True, group="library", exit_on_error=False)
    def _fetch_library(self, sections: list[str], token: int) -> None:
        fetched: dict[str, tuple[list[Any] | None, str]] = {}
        for section in sections:
            try:
                if section == "playlists":
                    items: Any = self.ytm.list_playlists()
                elif section == "albums":
                    items = self.ytm.library_albums()
                else:
                    items = self.ytm.library_artists()
            except Exception as exc:  # auth / network errors surface in the pane
                fetched[section] = (None, str(exc))
                continue
            fetched[section] = (items, "")
        self.call_from_thread(self._library_loaded, token, fetched)

    def _library_loaded(self, token: int, fetched: dict[str, tuple[list[Any] | None, str]]) -> None:
        if token != self._lib_token:
            return  # a reset (login / staleness) invalidated this fetch
        for section, (items, error) in fetched.items():
            if items is None:
                self._lib_state[section] = "error"
                self._lib_errors[section] = error
            else:
                self._lib_state[section] = "ready"
                self._lib_lists[section] = items
        self._lib_fetched_at = time.monotonic()
        if self._active_tab == "library":
            self._render_library()

    def _reset_library(self) -> None:
        """Drop cached library data (after login or when data goes stale)."""
        self._lib_token += 1  # any in-flight fetch becomes stale
        for section in LIBRARY_SECTIONS:
            self._lib_state[section] = "unloaded"
            self._lib_lists[section] = []
            self._lib_errors[section] = ""
        self._lib_fetched_at = None

    def _render_library(self) -> None:
        self._render_library_rows()
        # Drill in/out never moves focus, so refresh hints explicitly.
        self._sync_bar()

    def _render_library_rows(self) -> None:
        table = self.query_one("#library-table", TrackTable)
        cursor = table.cursor_row
        table.clear()
        if self._lib_drill is not None:
            self._render_library_drill(table)
            return
        section = self._lib_section
        if not self.ytm.authed:
            _placeholder(table, "Sign in (ctrl+l) to browse your library")
            return
        if self._lib_state[section] == "error":
            _placeholder(table, f"Load failed: {self._lib_errors[section]}")
            return
        if self._lib_state[section] != "ready":
            _placeholder(table, "Loading your library…")
            return
        items = self._lib_lists[section]
        if not items:
            _placeholder(table, f"No saved {section} yet — save some in YouTube Music")
            return
        if section == "playlists":
            for playlist in items:
                count = f"{playlist.count} songs" if playlist.count else ""
                table.add_row(playlist.title, "", count, "")
        elif section == "albums":
            for album in items:
                table.add_row(album.title, album.artist, album.year, "")
        else:
            for artist in items:
                table.add_row(artist.name, artist.detail, "", "")
        if table.row_count:
            table.move_cursor(row=min(max(cursor, 0), table.row_count - 1))

    def _render_library_drill(self, table: TrackTable) -> None:
        if self._lib_drill_error:
            _placeholder(table, f"Load failed: {self._lib_drill_error}")
            return
        if self._lib_drill_loading:
            _placeholder(table, f"Loading “{self._lib_drill.title}”…")
            return
        if not self._lib_tracks:
            _placeholder(table, "Playlist is empty")
            return
        self._add_track_rows(table, self._lib_tracks)
        if table.row_count:
            table.move_cursor(row=0)

    def _library_row_selected(self, row: int) -> None:
        if self._lib_drill is not None:
            if 0 <= row < len(self._lib_tracks):
                self._play_now(self._lib_tracks[row])
            return
        if self._lib_section != "playlists":
            self.notify(
                "Album and artist detail is not in this version yet",
                title="Library",
                timeout=4,
                markup=False,
            )
            return
        playlists = self._lib_lists["playlists"]
        if 0 <= row < len(playlists):
            self._open_library_playlist(playlists[row])

    def _open_library_playlist(self, playlist: Playlist) -> None:
        self._lib_drill = playlist
        self._lib_tracks = []
        self._lib_drill_loading = True
        self._lib_drill_error = ""
        self._lib_token += 1  # a section refresh would invalidate this drill
        self._render_library()
        self._fetch_playlist(playlist.playlist_id, self._lib_token)

    @work(thread=True, exclusive=True, group="library-drill", exit_on_error=False)
    def _fetch_playlist(self, playlist_id: str, token: int) -> None:
        try:
            tracks = self.ytm.playlist_tracks(playlist_id)
        except Exception as exc:
            self.call_from_thread(self._playlist_loaded, token, [], str(exc))
            return
        self.call_from_thread(self._playlist_loaded, token, tracks, "")

    def _playlist_loaded(self, token: int, tracks: list[Track], error: str) -> None:
        if token != self._lib_token:
            return
        self._lib_drill_loading = False
        self._lib_drill_error = error
        self._lib_tracks = tracks
        if self._active_tab == "library":
            self._render_library()

    def _close_library_drill(self) -> None:
        self._lib_drill = None
        self._lib_drill_loading = False
        self._lib_drill_error = ""
        self._lib_tracks = []

    # -- history tab ---------------------------------------------------------
    def _ensure_history(self) -> None:
        """Render history; fetch on first visit and refetch when stale."""
        if (
            self._history_fetched_at is not None
            and time.monotonic() - self._history_fetched_at > LIBRARY_STALE_SECONDS
        ):
            self._reset_history()
        if not self.ytm.authed:
            self._render_history()
            return
        if self._history_state == "unloaded":
            self._history_state = "loading"
            self._history_token += 1
            self._fetch_history(self._history_token)
        self._render_history()

    @work(thread=True, exclusive=True, group="history", exit_on_error=False)
    def _fetch_history(self, token: int) -> None:
        try:
            entries = self.ytm.history()
        except Exception as exc:  # auth / network errors surface in the pane
            self.call_from_thread(self._history_loaded, token, [], str(exc))
            return
        self.call_from_thread(self._history_loaded, token, entries, "")

    def _history_loaded(self, token: int, entries: list[HistoryEntry], error: str) -> None:
        if token != self._history_token:
            return  # a reset (login / staleness) invalidated this fetch
        self._history_fetched_at = time.monotonic()
        if error:
            self._history_state = "error"
            self._history_error = error
        else:
            self._history_state = "ready"
            self._history = entries
        if self._active_tab == "history":
            self._render_history()

    def _reset_history(self) -> None:
        self._history_token += 1  # any in-flight fetch becomes stale
        self._history = []
        self._history_state = "unloaded"
        self._history_error = ""
        self._history_fetched_at = None

    def _render_history(self) -> None:
        self._render_history_rows()
        self._sync_bar()

    def _render_history_rows(self) -> None:
        table = self.query_one("#history-table", TrackTable)
        cursor = table.cursor_row
        table.clear()
        if not self.ytm.authed:
            _placeholder(table, "Sign in (ctrl+l) to see your listening history", cells=5)
            return
        if self._history_state == "error":
            _placeholder(table, f"Load failed: {self._history_error}", cells=5)
            return
        if self._history_state != "ready":
            _placeholder(table, "Loading your listening history…", cells=5)
            return
        if not self._history:
            _placeholder(table, "No listening history yet — play something", cells=5)
            return
        current = self.queue.current
        for entry in self._history:
            track = entry.track
            playing = current is not None and track.video_id == current.video_id
            marked = track.video_id in self._marked
            table.add_row(
                entry.played,
                _title_cell(track.title, playing, marked),
                Text(track.artist_str, style="green") if playing else track.artist_str,
                track.album or "",
                track.duration_str,
            )
        if table.row_count:
            table.move_cursor(row=min(max(cursor, 0), table.row_count - 1))

    # -- actions ------------------------------------------------------------
    def _active_table(self) -> TrackTable | None:
        """Table of the active tab (none while typing in the search input)."""
        if isinstance(self.focused, Input):
            return None
        table_id = _TAB_TABLE.get(self._active_tab)
        if table_id is None:
            return None
        return self.query_one(table_id, TrackTable)

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_show_tab(self, tab_id: str) -> None:
        if tab_id not in _TAB_TABLE:
            return
        self.query_one("#tabs", TabbedContent).active = tab_id
        self.query_one(_TAB_TABLE[tab_id], TrackTable).focus()

    def action_show_results(self) -> None:
        self.action_show_tab("results")

    def action_show_queue(self) -> None:
        self.action_show_tab("queue")

    # -- vim navigation ------------------------------------------------------
    def action_cursor_down(self) -> None:
        table = self._active_table()
        if table is not None and table.row_count:
            table.move_cursor(row=min(table.cursor_row + 1, table.row_count - 1))

    def action_cursor_up(self) -> None:
        table = self._active_table()
        if table is not None and table.row_count:
            table.move_cursor(row=max(table.cursor_row - 1, 0))

    def action_g_prefix(self) -> None:
        """`g` starts the gg sequence; a second `g` within 1s jumps to top."""
        if self._g_pending:
            self._clear_g_prefix()
            self.action_scroll_first()
            return
        self._g_pending = True
        if self._g_timer is not None:
            self._g_timer.stop()
        self._g_timer = self.set_timer(G_PREFIX_TIMEOUT, self._clear_g_prefix)

    def _clear_g_prefix(self) -> None:
        self._g_pending = False
        self._g_timer = None

    def action_scroll_first(self) -> None:
        table = self._active_table()
        if table is not None and table.row_count:
            table.move_cursor(row=0)

    def action_scroll_last(self) -> None:
        table = self._active_table()
        if table is not None and table.row_count:
            table.move_cursor(row=table.row_count - 1)

    def _visible_rows(self, table: TrackTable) -> int:
        height = table.size.height or 20
        return max(1, (height - 1) // 2)

    def action_half_page_down(self) -> None:
        table = self._active_table()
        if table is not None and table.row_count:
            step = self._visible_rows(table)
            table.move_cursor(row=min(table.cursor_row + step, table.row_count - 1))

    def action_half_page_up(self) -> None:
        table = self._active_table()
        if table is not None and table.row_count:
            step = self._visible_rows(table)
            table.move_cursor(row=max(table.cursor_row - step, 0))

    def action_page_down(self) -> None:
        table = self._active_table()
        if table is not None and table.row_count:
            table.action_page_down()

    def action_page_up(self) -> None:
        table = self._active_table()
        if table is not None and table.row_count:
            table.action_page_up()

    def action_cycle_theme(self) -> None:
        names = list(self.available_themes)
        if not names:
            return
        try:
            nxt = (names.index(self.theme) + 1) % len(names)
        except ValueError:
            nxt = 0
        self.theme = names[nxt]
        self.notify(f"theme: {names[nxt]}", timeout=2, markup=False)

    def action_go_back(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one("#tabs", TabbedContent).active = "results"
            self.query_one("#results-table", TrackTable).focus()
            return
        if self._active_tab == "library" and self._lib_drill is not None:
            self._close_library_drill()
            self._render_library()
            table = self.query_one("#library-table", TrackTable)
            table.focus()
            if table.row_count:
                table.move_cursor(row=0)

    def action_toggle_pause(self) -> None:
        if self.queue.current is None:
            if len(self.queue):
                self._load_current()
            return
        if not self.playing:
            self._load_current()
            return
        try:
            self.mpv.play_pause()
        except MpvError as exc:
            self.notify(str(exc), severity="error", markup=False)

    def action_next_track(self) -> None:
        if not self.playing:
            self._load_current()
            return
        self._advance()

    def action_prev_track(self) -> None:
        if not self.playing:
            self._load_current()
            return
        if (self.position or 0) > 3:
            self.action_seek_to_start()
            return
        if self.queue.prev() is not None:
            self._rebuild_queue()
            self._load_current()
        else:
            self.action_seek_to_start()

    def action_seek_to_start(self) -> None:
        try:
            self.mpv.seek_relative(-(self.position or 0))
        except MpvError as exc:
            self.notify(str(exc), severity="error", markup=False)

    def action_seek_forward(self) -> None:
        self._seek(SEEK_STEP)

    def action_seek_backward(self) -> None:
        self._seek(-SEEK_STEP)

    def _seek(self, seconds: float) -> None:
        if not self.playing:
            return
        try:
            self.mpv.seek_relative(seconds)
        except MpvError as exc:
            self.notify(str(exc), severity="error", markup=False)

    def action_volume_up(self) -> None:
        self._set_volume(self.volume + VOLUME_STEP)

    def action_volume_down(self) -> None:
        self._set_volume(self.volume - VOLUME_STEP)

    def _set_volume(self, value: int) -> None:
        if not self.mpv.is_running:
            return
        try:
            self.mpv.set_volume(value)
        except MpvError as exc:
            self.notify(str(exc), severity="error", markup=False)

    def action_enqueue(self) -> None:
        if not self._marked and self._active_tab == "queue":
            return  # `a` on the queue tab only acts on marked songs
        tracks = self._targets()
        if not tracks:
            return  # placeholder / empty rows
        for track in tracks:
            self.queue.enqueue(track)
        self._rebuild_queue()
        if len(tracks) == 1:
            self.notify(f"Queued: {tracks[0].title}", timeout=3, markup=False)
        else:
            self.notify(f"Queued {len(tracks)} songs", timeout=3, markup=False)

    # -- selection (marks) ---------------------------------------------------
    def _cursor_track(self) -> Track | None:
        """Track under the cursor of the active table (None for placeholders)."""
        table = self._active_table()
        if table is None:
            return None
        tab = self._active_tab
        if tab == "profile":
            return None  # stats rows are not playable tracks
        row = table.cursor_row
        if tab == "queue":
            return self.queue.items[row] if 0 <= row < len(self.queue) else None
        if tab == "history":
            return self._history[row].track if 0 <= row < len(self._history) else None
        if tab == "library":
            if self._lib_drill is None:
                return None  # playlist/album/artist rows are not tracks
            return self._lib_tracks[row] if 0 <= row < len(self._lib_tracks) else None
        return self._results[row] if 0 <= row < len(self._results) else None

    def _marked_tracks(self) -> list[Track]:
        """Marked tracks in display order (results first), de-duplicated."""
        if not self._marked:
            return []
        seen: set[str] = set()
        out: list[Track] = []
        history_tracks = (entry.track for entry in self._history)
        for track in [*self._results, *self.queue.items, *self._lib_tracks, *history_tracks]:
            if track.video_id in self._marked and track.video_id not in seen:
                seen.add(track.video_id)
                out.append(track)
        return out

    def _targets(self) -> list[Track]:
        """Marked songs, falling back to the single song under the cursor."""
        marked = self._marked_tracks()
        if marked:
            return marked
        cursor_track = self._cursor_track()
        return [cursor_track] if cursor_track is not None else []

    def action_toggle_mark(self) -> None:
        track = self._cursor_track()
        if track is None:
            return
        if track.video_id in self._marked:
            self._marked.discard(track.video_id)
        else:
            self._marked.add(track.video_id)
        self._rebuild_active()
        self._sync_bar()

    def _rebuild_active(self) -> None:
        tab = self._active_tab
        if tab == "queue":
            self._rebuild_queue()
        elif tab == "library":
            self._render_library()
        elif tab == "history":
            self._render_history()
        else:
            self._rebuild_results()

    def action_clear_marks(self) -> None:
        if not self._marked:
            return
        self._marked.clear()
        self._rebuild_results()
        self._rebuild_queue()
        if self._active_tab in ("library", "history"):
            self._rebuild_active()
        self._sync_bar()

    # -- playlist add --------------------------------------------------------
    def action_add_to_playlist(self) -> None:
        tracks = self._targets()
        if not tracks:
            self.notify("Nothing selected — s marks songs", timeout=4, markup=False)
            return
        self._pending_add = tracks
        self.push_screen(PlaylistPicker(len(tracks)), self._playlist_chosen)

    def _playlist_chosen(self, result: str | None) -> None:
        tracks = self._pending_add or []
        self._pending_add = None
        if result is None or not tracks:
            return
        if result == PlaylistPicker.LOGIN:
            self.action_login()
            return
        title = self._playlist_title(result)
        self._add_to_playlist(result, [t.video_id for t in tracks], title, len(tracks))

    def _playlist_title(self, playlist_id: str) -> str:
        for playlist in self._playlists or []:
            if playlist.playlist_id == playlist_id:
                return playlist.title
        return playlist_id

    @work(thread=True, exclusive=True, group="add-to-playlist")
    def _add_to_playlist(
        self, playlist_id: str, video_ids: list[str], title: str, count: int
    ) -> None:
        try:
            self.ytm.add_to_playlist(playlist_id, video_ids)
        except Exception as exc:
            self.call_from_thread(self._add_failed, title, str(exc))
            return
        self.call_from_thread(self._add_done, title, count)

    def _add_done(self, title: str, count: int) -> None:
        self._marked.clear()
        self._rebuild_results()
        self._rebuild_queue()
        noun = "song" if count == 1 else "songs"
        self.notify(
            f"Added {count} {noun} → {title}",
            title="Playlist",
            timeout=4,
            markup=False,
        )

    def _add_failed(self, title: str, error: str) -> None:
        self.notify(
            f"Add to “{title}” failed: {error}",
            title="Playlist",
            severity="error",
            timeout=8,
            markup=False,
        )

    def action_remove(self) -> None:
        if self.query_one("#tabs", TabbedContent).active != "queue":
            return
        table = self.query_one("#queue-table", TrackTable)
        row = table.cursor_row
        if row < 0 or row >= len(self.queue):
            return
        was_current = row == self.queue.cursor
        removed = self.queue.remove(row)
        if removed is None:
            return
        self._rebuild_queue()
        if was_current:
            if self.queue.current is not None:
                self._load_current()
            else:
                self._stop_playback()
        self.notify(f"Removed: {removed.title}", timeout=3, markup=False)

    def _stop_playback(self) -> None:
        self.playing = False
        self.paused = False
        self.position = None
        self.duration = None
        self._rebuild_results()
        self._update_art(None)
        if self.mpv.is_running:
            try:
                self.mpv.command("stop")
            except MpvError:
                pass
        self._sync_bar()

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    # -- login ---------------------------------------------------------------
    def action_login(self) -> None:
        if self.ytm.authed:
            self.notify("Already logged in", timeout=3)
            return
        self.push_screen(LoginScreen(), self._login_done)

    def _login_done(self, success: bool) -> None:
        if not success:
            self.notify(
                "Login not completed — press ctrl+l to retry",
                title="Login",
                severity="warning",
                timeout=5,
            )
            return
        if self.ytm.enable_auth():
            self._playlists = None  # stale guest cache
            self._reset_library()  # guest placeholder → real library data
            self._reset_history()
            info = self._pending_account
            self._pending_account = None
            if info:
                name = str(info.get("accountName") or "")
                handle = str(info.get("channelHandle") or "")
                who = f"{name} {handle}".strip()
                self.notify(
                    f"Logged in as {who or 'YouTube Music'} — playlists available",
                    title="Account",
                    timeout=5,
                )
            else:
                self.notify("Logged in — saved playlists available", title="Account", timeout=4)
        else:
            self.notify(
                "Saved login could not be loaded",
                title="Login",
                severity="error",
                timeout=8,
            )
        self._sync_bar()
