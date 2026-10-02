"""Textual application shell: search, queue, playback controls, player bar."""

from __future__ import annotations

import os
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Header, Input, TabbedContent, TabPane

from . import art
from .models import Playlist, Track
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


def _placeholder(table: TrackTable, message: str) -> None:
    """Add a dimmed single-cell status/placeholder row."""
    table.add_row("", Text(message, style="dim"), "", "")


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
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search", show=False),
        Binding("r", "show_results", "Results", show=False),
        Binding("q", "show_queue", "Queue", show=False),
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
            hints=hints_for(mode, self._active_tab),
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

    def _rebuild_results(self) -> None:
        """Re-render result rows, marking the playing track with ▶."""
        if not self._results:
            return
        table = self.query_one("#results-table", TrackTable)
        cursor = table.cursor_row
        table.clear()
        current = self.queue.current
        for track in self._results:
            playing = current is not None and track.video_id == current.video_id
            marked = track.video_id in self._marked
            table.add_row(
                _title_cell(track.title, playing, marked),
                Text(track.artist_str, style="green") if playing else track.artist_str,
                track.album or "",
                track.duration_str,
            )
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

    def _play_now(self, track: Track) -> None:
        for index, queued in enumerate(self.queue.items):
            if queued.video_id == track.video_id:
                self.queue.select(index)
                break
        else:
            self.queue.enqueue(track)
        self._rebuild_queue()
        self._load_current()

    # -- actions ------------------------------------------------------------
    def _active_table(self) -> TrackTable | None:
        """Table of the active tab (none while typing in the search input)."""
        if isinstance(self.focused, Input):
            return None
        tid = "#queue-table" if self._active_tab == "queue" else "#results-table"
        return self.query_one(tid, TrackTable)

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_show_results(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "results"
        self.query_one("#results-table", TrackTable).focus()

    def action_show_queue(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "queue"
        self.query_one("#queue-table", TrackTable).focus()

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
        if not self._marked and self._active_tab != "results":
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
        row = table.cursor_row
        if self._active_tab == "queue":
            return self.queue.items[row] if 0 <= row < len(self.queue) else None
        return self._results[row] if 0 <= row < len(self._results) else None

    def _marked_tracks(self) -> list[Track]:
        """Marked tracks in display order (results first), de-duplicated."""
        if not self._marked:
            return []
        seen: set[str] = set()
        out: list[Track] = []
        for track in [*self._results, *self.queue.items]:
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
        if self._active_tab == "queue":
            self._rebuild_queue()
        else:
            self._rebuild_results()
        self._sync_bar()

    def action_clear_marks(self) -> None:
        if not self._marked:
            return
        self._marked.clear()
        self._rebuild_results()
        self._rebuild_queue()
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
            return
        if self.ytm.enable_auth():
            self._playlists = None  # stale guest cache
            self.notify("Logged in — saved playlists available", title="Account", timeout=4)
        else:
            self.notify(
                "Token stored but could not be loaded",
                title="Login",
                severity="error",
                timeout=8,
            )
        self._sync_bar()
