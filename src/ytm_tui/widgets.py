"""Statusline / player bar and help overlay widgets."""

from __future__ import annotations

import threading
import webbrowser
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from . import art, auth
from .models import Playlist, Track, format_duration

PROGRESS_WIDTH = 24


def _progress_bar(position: float | None, duration: float | None) -> str:
    if not duration or duration <= 0 or position is None:
        return "░" * PROGRESS_WIDTH
    frac = min(1.0, max(0.0, position / duration))
    filled = round(frac * PROGRESS_WIDTH)
    return "█" * filled + "░" * (PROGRESS_WIDTH - filled)


def hints_for(mode: str, tab: str) -> str:
    """Context-sensitive key hints for the hint line."""
    if mode == "insert":
        return "type to search · enter → results · esc → normal"
    common = "j/k · gg/G · ctrl+d/u page · space pause · t theme · ? help"
    if tab == "queue":
        return "enter jump · d remove · s mark · A playlist · r/q tabs · n/p · " + common
    return "enter play · a queue · s mark · A playlist · / search · h/l · n/p · " + common


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return text[0]
    return text[: width - 1] + "…"


class PlayerBar(Static):
    """Persistent bottom bar: vim statusline + context hints."""

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
        self.mode = "normal"
        self.marked = 0
        self.authed = False
        self.hints = hints_for("normal", "results")
        self.update(self._render_state())

    def sync(self, **state: Any) -> None:
        for name, value in state.items():
            setattr(self, name, value)
        self.update(self._render_state())

    def _terminal_width(self) -> int:
        try:
            # CSS gives #player-bar `padding: 0 1` → content width is 2 less.
            return max(40, self.app.size.width - 2)
        except Exception:
            return 100

    def _account_chip(self) -> Text:
        chips = Text()
        if self.marked:
            chips.append(f"  ●{self.marked}", style="bold yellow")
        if self.authed:
            chips.append("  auth", style="green")
        else:
            chips.append("  guest", style="dim")
        return chips

    def _render_state(self) -> Text:
        width = self._terminal_width()
        out = Text()
        mode = self.mode if self.mode in ("normal", "insert") else "normal"
        chip = f"-- {mode.upper()} --"
        chip_style = "bold black on green" if mode == "normal" else "bold black on yellow"
        out.append(f" {chip} ", style=chip_style)
        out.append(" │ ", style="dim")

        if self.track is None:
            out.append("■ ", style="bold dim")
            out.append("Nothing playing", style="bold dim")
            out.append("  — press / to search", style="dim")
            out.append(self._account_chip())
        else:
            icon = "⏸" if self.paused else "▶"
            if not self.playing:
                icon = "■"
            icon_style = "bold green" if icon == "▶" else "yellow"
            if icon == "■":
                icon_style = "bold dim"
            out.append(f"{icon} ", style=icon_style)

            rest: list[tuple[str, str]] = []
            artist = f" — {self.track.artist_str}" if self.track.artist_str else ""
            rest.append((artist, "cyan"))
            rest.append(
                (
                    f"   {format_duration(int(self.position or 0))}"
                    f"/{format_duration(int(self.duration) if self.duration else None)} ",
                    "dim",
                )
            )
            rest.append((_progress_bar(self.position, self.duration), "green"))
            rest.append((f"  vol {self.volume}% ", "magenta"))
            if self.queue_length:
                rest.append((f"[{self.queue_index}/{self.queue_length}]", "yellow"))

            account = self._account_chip()
            used = len(chip) + 7 + sum(len(text) for text, _ in rest) + len(account)
            title_budget = max(8, width - used)
            out.append(_truncate(self.track.title, title_budget), style="bold")
            for text, style in rest:
                out.append(text, style=style)
            out.append(account)

        out.append("\n")
        out.append(_truncate(self.hints, width), style="dim")
        return out


HELP_TEXT = """\
[b]Navigation[/b]
  j / k · ↓ / ↑        move cursor
  gg / G               first / last row
  ctrl+d / ctrl+f      half / full page down
  ctrl+u / ctrl+b      half / full page up
  /                    focus search (INSERT mode)
  esc                  back to NORMAL mode
  r                    Results tab
  q                    Queue tab
  tab                  cycle focus

[b]Playback[/b]
  space                play / pause
  n / p                next / previous  (p restarts after 3s)
  h / l · ← / →        seek -5s / +5s
  + / -                volume up / down

[b]Selection[/b]
  s                    toggle ● mark on a song
  S                    clear all marks

[b]Queue[/b]
  a                    queue marked songs (or the cursor row)
  d                    remove selected queue entry
  enter                play now / jump to entry

[b]Playlists[/b]
  A                    add marked songs to a playlist
  ctrl+l               sign in (Google device code)
  ctrl+b               inside login: switch to browser sign-in

[b]Other[/b]
  t                    cycle theme
  ?                    toggle this help
  ctrl+q               quit
"""


class HelpScreen(ModalScreen[None]):
    """Full-screen key reference, vim-help style. Close with ?/esc/q."""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close", show=False),
        Binding("?", "dismiss_screen", show=False),
        Binding("q", "dismiss_screen", show=False),
        Binding("j", "scroll_down", show=False),
        Binding("down", "scroll_down", show=False),
        Binding("k", "scroll_up", show=False),
        Binding("up", "scroll_up", show=False),
        Binding("ctrl+d", "half_down", show=False),
        Binding("ctrl+f", "page_down", show=False),
        Binding("ctrl+u", "half_up", show=False),
        Binding("ctrl+b", "page_up", show=False),
        Binding("g", "scroll_top", show=False),
        Binding("G", "scroll_bottom", show=False),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-box"):
            yield Static(HELP_TEXT, id="help-content")

    def _box(self) -> VerticalScroll:
        return self.query_one("#help-box", VerticalScroll)

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def action_scroll_down(self) -> None:
        self._box().scroll_relative(y=3, animate=False)

    def action_scroll_up(self) -> None:
        self._box().scroll_relative(y=-3, animate=False)

    def action_half_down(self) -> None:
        self._box().scroll_relative(y=max(3, self._box().size.height // 2), animate=False)

    def action_half_up(self) -> None:
        self._box().scroll_relative(y=-max(3, self._box().size.height // 2), animate=False)

    def action_page_down(self) -> None:
        self._box().page_down(animate=False)

    def action_page_up(self) -> None:
        self._box().page_up(animate=False)

    def action_scroll_top(self) -> None:
        self._box().scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        self._box().scroll_end(animate=False)


CREDS_HELP = """\
[b]One-time setup — Google Cloud Console[/b]
  1. console.cloud.google.com → APIs & Services → Credentials
  2. Create credentials → OAuth client ID
  3. Application type: [b]TVs and Limited Input devices[/b]
  4. Paste the client ID and secret below

Stored only in ~/.config/yt-tui-player/ (mode 600).
"""

FLOW_HELP = """\
[b]Approve the login[/b]
  Open the URL below on any device, enter the code,
  and pick your YouTube Music account.
"""

BROWSER_HELP = """\
[b]Sign in with your browser session[/b]
  1. Open music.youtube.com and sign in
  2. F12 → Network → pick a youtubei/v1 request
     (browse, next or search work best)
  3. Right-click → Copy → Copy as cURL (bash)
  4. Paste below (whole command) and press enter

Firefox: Copy → Copy Request Headers works too.
No Google Cloud setup needed. Re-paste when the session expires.
"""


class LoginScreen(ModalScreen[bool]):
    """Sign-in: Google device-code OAuth, or paste browser-session headers.

    Dismisses True on success, False on cancel. `ctrl+b` toggles the method
    while on the credentials stage; all network work runs in workers.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("q", "cancel", "Cancel", show=False),
        Binding("enter", "confirm", "Continue", show=False, priority=True),
        Binding("ctrl+b", "toggle_mode", "Switch method", show=False),
        Binding("o", "open_browser", "Open browser", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.stage = "creds"  # creds | code | polling
        self.mode = "oauth"  # oauth | browser
        self._cancelled = threading.Event()
        self._code: auth.DeviceCode | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="login-box"):
            yield Static(id="login-help")
            yield Input(placeholder="OAuth client ID", id="client-id")
            yield Input(placeholder="OAuth client secret", password=True, id="client-secret")
            yield TextArea(id="headers-paste")
            yield Static(id="login-status")
            yield Static(id="login-hints")

    def on_mount(self) -> None:
        saved = auth.load_client()
        if saved is not None:
            self.query_one("#client-id", Input).value = saved[0]
            self.query_one("#client-secret", Input).value = saved[1]
        self._set_stage("creds")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "client-id":
            self.query_one("#client-secret", Input).focus()
        else:
            self.action_confirm()

    # -- state ---------------------------------------------------------------
    def _set_stage(self, stage: str) -> None:
        self.stage = stage
        self._apply_view()

    def _apply_view(self) -> None:
        show_creds = self.stage == "creds"
        browser = self.mode == "browser"
        self.query_one("#client-id", Input).display = show_creds and not browser
        self.query_one("#client-secret", Input).display = show_creds and not browser
        self.query_one("#headers-paste", TextArea).display = show_creds and browser
        if not show_creds:
            self.query_one("#login-hints", Static).update(
                Text("o open browser · esc cancel", style="dim")
            )
            return
        self.query_one("#login-help", Static).update(
            Text.from_markup(BROWSER_HELP if browser else CREDS_HELP)
        )
        if browser:
            self.query_one("#login-hints", Static).update(
                Text("enter sign in · ctrl+b google sign-in · esc cancel", style="dim")
            )
            self.query_one("#headers-paste", TextArea).focus()
        else:
            self.query_one("#login-hints", Static).update(
                Text("enter continue · ctrl+b browser sign-in · esc cancel", style="dim")
            )
            self.query_one("#client-id", Input).focus()

    def _set_status(self, text: Text) -> None:
        self.query_one("#login-status", Static).update(text)

    # -- flow ----------------------------------------------------------------
    def action_confirm(self) -> None:
        if self.stage != "creds":
            return
        if self.mode == "browser":
            self._submit_headers()
            return
        if self.focused is not None and self.focused.id == "client-id":
            self.query_one("#client-secret", Input).focus()
            return
        client_id = self.query_one("#client-id", Input).value.strip()
        client_secret = self.query_one("#client-secret", Input).value.strip()
        if not client_id or not client_secret:
            self._set_status(Text("Both client ID and secret are required", style="red"))
            return
        auth.save_client(client_id, client_secret)
        self._start_flow()

    def _start_flow(self) -> None:
        self._set_stage("code")
        self.query_one("#login-help", Static).update(Text.from_markup(FLOW_HELP))
        self._set_status(Text("Requesting login code…", style="dim"))
        self._request_code()

    @work(thread=True, exclusive=True, group="login")
    def _request_code(self) -> None:
        try:
            code = auth.begin_device_flow()
        except auth.PollError as exc:
            self.app.call_from_thread(self._flow_failed, str(exc))
            return
        except Exception as exc:
            self.app.call_from_thread(self._flow_failed, f"Could not get login code: {exc}")
            return
        self.app.call_from_thread(self._show_code, code)

    def _show_code(self, code: auth.DeviceCode) -> None:
        if not self.is_attached:
            return
        self._code = code
        self._set_stage("polling")
        status = Text()
        status.append(f"  {code.url}\n", style="bold underline")
        status.append(f"  code: {code.user_code}\n", style="bold yellow")
        status.append(
            f"  waiting for approval ({code.expires_in}s timeout)…",
            style="dim",
        )
        self._set_status(status)
        self._poll(code)

    @work(thread=True, exclusive=True, group="login")
    def _poll(self, code: auth.DeviceCode) -> None:
        try:
            auth.poll_for_token(code, should_cancel=self._cancelled.is_set)
        except auth.CancelledLogin:
            return
        except auth.PollError as exc:
            self.app.call_from_thread(self._flow_failed, str(exc))
            return
        except Exception as exc:
            self.app.call_from_thread(self._flow_failed, f"Login failed: {exc}")
            return
        self.app.call_from_thread(self._login_succeeded)

    def _flow_failed(self, message: str) -> None:
        if not self.is_attached:
            return
        self._set_stage("creds")
        self._set_status(Text(message, style="red"))

    def _login_succeeded(self) -> None:
        if not self.is_attached:
            return
        self.dismiss(True)

    # -- browser-session sign-in --------------------------------------------
    def _submit_headers(self) -> None:
        raw = self.query_one("#headers-paste", TextArea).text.strip()
        if not raw:
            self._set_status(
                Text("Paste the headers or a Copy as cURL (bash) command", style="red")
            )
            return
        self._set_status(Text("Verifying session…", style="dim"))
        self._verify_headers(raw)

    @work(thread=True, exclusive=True, group="login")
    def _verify_headers(self, raw: str) -> None:
        try:
            auth.save_browser_headers(raw)
            info = auth.verify_browser_session()
        except auth.HeadersError as exc:
            self.app.call_from_thread(self._flow_failed, str(exc))
            return
        except Exception as exc:
            self.app.call_from_thread(self._flow_failed, f"Sign-in failed: {exc}")
            return
        self.app.call_from_thread(self._headers_ok, info)

    def _headers_ok(self, info: dict) -> None:
        if not self.is_attached:
            return
        self.app._pending_account = info  # consumed by _login_done for the toast
        self.dismiss(True)

    # -- keys ----------------------------------------------------------------
    def action_toggle_mode(self) -> None:
        if self.stage != "creds":
            return
        self.mode = "browser" if self.mode == "oauth" else "oauth"
        self._set_status(Text(""))
        self._apply_view()

    def action_cancel(self) -> None:
        self._cancelled.set()
        self.dismiss(False)

    def action_open_browser(self) -> None:
        if self._code is None:
            return
        webbrowser.open(self._code.url)


class NowPlayingPanel(Vertical):
    """Right-hand dock: album cover rendered in half-blocks + track meta."""

    def compose(self) -> ComposeResult:
        yield Static(art.placeholder(), id="art-image")
        yield Static(id="art-meta")

    def show_art(self, rendered: Text | None) -> None:
        self.query_one("#art-image", Static).update(
            rendered if rendered is not None else art.placeholder()
        )

    def show_track(self, track: Track | None) -> None:
        meta = Text()
        if track is None:
            meta.append("No cover", style="dim")
        else:
            meta.append(track.title, style="bold")
            meta.append("\n")
            meta.append(track.artist_str, style="cyan")
            if track.duration:
                meta.append("\n")
                meta.append(format_duration(track.duration), style="dim")
        self.query_one("#art-meta", Static).update(meta)


class PlaylistPicker(ModalScreen[str | None]):
    """Choose which playlist to add the selected songs to.

    Dismisses with the playlist id, `PlaylistPicker.LOGIN` (guest row), or
    None on cancel. Loads the user's playlists lazily via a worker.
    """

    LOGIN = "\x00login"

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("q", "cancel", "Cancel", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("up", "cursor_up", show=False),
        Binding("g", "first", "First", show=False),
        Binding("G", "last", "Last", show=False),
    ]

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count
        self._playlists: list[Playlist] = []
        self._state = "loading"  # loading | ready | guest | error

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="picker-box"):
            yield Static(id="picker-help")
            yield OptionList(id="picker-list")

    def on_mount(self) -> None:
        self._show_help(style="bold")
        self.query_one("#picker-list", OptionList).focus()
        self._start()

    # -- loading -------------------------------------------------------------
    def _show_help(self, message: str | None = None, style: str = "bold") -> None:
        if message is None:
            plural = "song" if self.count == 1 else "songs"
            message = f"Add {self.count} {plural} to:"
        self.query_one("#picker-help", Static).update(Text(message, style=style))

    def _set_options(self, options: list[Option]) -> None:
        option_list = self.query_one("#picker-list", OptionList)
        option_list.clear_options()
        option_list.add_options(options)
        if option_list.option_count and option_list.highlighted is None:
            option_list.highlighted = 0
        option_list.focus()

    def _start(self) -> None:
        if not self.app.ytm.authed:
            self._state = "guest"
            self._show_help("Not logged in — playlists require Google sign-in")
            self._set_options([Option("Sign in — press enter")])
            return
        cached = self.app._playlists
        if cached is not None:
            self._show_playlists(cached)
            return
        self._state = "loading"
        self._set_options([Option("Loading playlists…", disabled=True)])
        self._fetch()

    @work(thread=True, exclusive=True, group="playlists")
    def _fetch(self) -> None:
        try:
            playlists = self.app.ytm.list_playlists()
        except Exception as exc:  # network / auth errors surface in the picker
            self.app.call_from_thread(self._fetch_failed, str(exc))
            return
        self.app.call_from_thread(self._fetch_done, playlists)

    def _fetch_done(self, playlists: list[Playlist]) -> None:
        if not self.is_attached:
            return
        self.app._playlists = playlists
        self._show_playlists(playlists)

    def _fetch_failed(self, error: str) -> None:
        if not self.is_attached:
            return
        self._state = "error"
        self._show_help(f"Load failed: {error}  (enter retry · esc cancel)", style="red")
        self._set_options([Option("Retry loading", disabled=False)])

    def _show_playlists(self, playlists: list[Playlist]) -> None:
        self._playlists = list(playlists)
        self._state = "ready"
        if not playlists:
            self._show_help("No playlists found — create one in YouTube Music")
            self._set_options([Option("No playlists", disabled=True)])
            return
        self._show_help()
        options = []
        for playlist in self._playlists:
            label = Text(playlist.title)
            if playlist.count:
                label.append(f"  ({playlist.count})", style="dim")
            options.append(Option(label))
        self._set_options(options)

    # -- selection -----------------------------------------------------------
    @on(OptionList.OptionSelected)
    def _option_selected(self, event: OptionList.OptionSelected) -> None:
        if self._state == "guest":
            self.dismiss(PlaylistPicker.LOGIN)
        elif self._state == "error":
            self._retry()
        elif self._state == "ready":
            index = event.index
            if 0 <= index < len(self._playlists):
                self.dismiss(self._playlists[index].playlist_id)

    def _retry(self) -> None:
        self._show_help()
        self._state = "loading"
        self._set_options([Option("Loading playlists…", disabled=True)])
        self._fetch()

    # -- keys ----------------------------------------------------------------
    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#picker-list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#picker-list", OptionList).action_cursor_up()

    def action_first(self) -> None:
        self.query_one("#picker-list", OptionList).action_first()

    def action_last(self) -> None:
        self.query_one("#picker-list", OptionList).action_last()
