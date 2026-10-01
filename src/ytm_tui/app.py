"""Textual application shell: search, queue, playback controls, player bar."""

from __future__ import annotations

import os
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Header, Input, Static, TabbedContent, TabPane

from .models import Track, format_duration
from .mpv_client import MpvClient, MpvError
from .queue import QueueModel
from .ytm import YTMusicClient

SEARCH_DEBOUNCE = 0.4
SEEK_STEP = 5.0
VOLUME_STEP = 5
PROGRESS_WIDTH = 24
HINTS = (
    "/ search · q queue · tab focus · enter play · space pause · n/p next/prev · "
    "←/→ seek · +/- volume · a add · d remove · esc back · ctrl+q quit"
)


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


def _progress_bar(position: float | None, duration: float | None) -> str:
    if not duration or duration <= 0 or position is None:
        return "░" * PROGRESS_WIDTH
    frac = min(1.0, max(0.0, position / duration))
    filled = round(frac * PROGRESS_WIDTH)
    return "█" * filled + "░" * (PROGRESS_WIDTH - filled)


class PlayerBar(Static):
    """Persistent bottom bar: now playing, progress, volume, hints."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.track: Track | None = None
        self.position: float | None = None
        self.duration: float | None = None
        self.playing = False
        self.paused = False
        self.volume = 100
        self.queue_index = 0
        self.queue_length = 0
        self.update(self._render_state())

    def sync(self, **state: Any) -> None:
        for name, value in state.items():
            setattr(self, name, value)
        self.update(self._render_state())

    def _render_state(self) -> Text:
        out = Text()
        if self.track is None:
            out.append("■  Nothing playing", style="bold dim")
            out.append("  —  search something and press Enter")
        else:
            icon = "⏸" if self.paused else "▶"
            if not self.playing:
                icon = "■"
            out.append(f" {icon} ", style="bold green" if icon == "▶" else "yellow")
            out.append(self.track.title, style="bold")
            out.append(f" — {self.track.artist_str}   ", style="cyan")
            out.append(
                f"{format_duration(int(self.position or 0))}"
                f"/{format_duration(int(self.duration) if self.duration else None)} ",
                style="dim",
            )
            out.append(_progress_bar(self.position, self.duration), style="green")
            out.append(f"  vol {self.volume}%  ", style="magenta")
            if self.queue_length:
                out.append(f"[{self.queue_index}/{self.queue_length}]", style="yellow")
        out.append("\n")
        out.append(HINTS, style="dim")
        return out


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
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search", show=False),
        Binding("q", "show_queue", "Queue", show=False),
        Binding("space", "toggle_pause", "Play/Pause", show=False),
        Binding("n", "next_track", "Next", show=False),
        Binding("p", "prev_track", "Prev", show=False),
        Binding("left", "seek_backward", "Seek -5s", show=False),
        Binding("right", "seek_forward", "Seek +5s", show=False),
        Binding("+", "volume_up", "Volume +", show=False),
        Binding("=", "volume_up", show=False),
        Binding("-", "volume_down", "Volume -", show=False),
        Binding("a", "enqueue", "Add to queue", show=False),
        Binding("d", "remove", "Remove", show=False),
        Binding("escape", "go_back", "Back", show=False),
        Binding("?", "show_help", "Help", show=False),
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
        yield PlayerBar(id="player-bar")

    def on_mount(self) -> None:
        results = self.query_one("#results-table", TrackTable)
        results.add_column("Title", width=42)
        results.add_column("Artist", width=38)
        results.add_column("Album", width=22)
        results.add_column("Dur", width=6)
        results.add_row("", "Press / to search, Enter to play", "", "")
        queue = self.query_one("#queue-table", TrackTable)
        queue.add_column("#", width=4)
        queue.add_column("Title", width=50)
        queue.add_column("Artist", width=46)
        queue.add_column("Dur", width=6)
        self.query_one("#search", Input).focus()
        self._start_mpv()

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
    def _sync_bar(self) -> None:
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
        )

    def _load_current(self) -> None:
        track = self.queue.current
        if track is None:
            self.playing = False
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
        self._sync_bar()

    def _advance(self) -> None:
        if self.queue.next() is None:
            self.playing = False
            self.paused = False
            self.position = None
            self.duration = None
            self.notify("Queue finished", timeout=4)
            self._rebuild_queue()
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
            table.add_row("", "Press / to search, Enter to play", "", "")
            return
        self._search_token += 1
        token = self._search_token
        self._results = []
        table.clear()
        table.add_row("", f"Searching “{query}”…", "", "")
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
        table.clear()
        if not tracks:
            table.add_row("", "No results", "", "")
            return
        for track in tracks:
            table.add_row(track.title, track.artist_str, track.album or "", track.duration_str)
        table.move_cursor(row=0)

    def _search_failed(self, token: int, error: str) -> None:
        if token != self._search_token:
            return
        table = self.query_one("#results-table", TrackTable)
        table.clear()
        table.add_row("", f"Search failed: {error}", "", "")

    # -- queue table --------------------------------------------------------
    def _rebuild_queue(self) -> None:
        table = self.query_one("#queue-table", TrackTable)
        table.clear()
        for index, track in enumerate(self.queue.items, start=1):
            table.add_row(str(index), track.title, track.artist_str, track.duration_str)
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
    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_show_queue(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "queue"
        self.query_one("#queue-table", TrackTable).focus()

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
        if self.query_one("#tabs", TabbedContent).active != "results":
            return
        table = self.query_one("#results-table", TrackTable)
        row = table.cursor_row
        if not (0 <= row < len(self._results)):
            return
        track = self._results[row]
        self.queue.enqueue(track)
        self._rebuild_queue()
        self.notify(f"Queued: {track.title}", timeout=3, markup=False)

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
        if self.mpv.is_running:
            try:
                self.mpv.command("stop")
            except MpvError:
                pass
        self._sync_bar()

    def action_show_help(self) -> None:
        self.notify(HINTS, title="Keys", timeout=10, markup=False)
