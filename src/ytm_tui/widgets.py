"""Statusline / player bar and help overlay widgets."""

from __future__ import annotations

import threading
import webbrowser
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from . import auth
from .models import Track, format_duration

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
    common = "j/k move · gg/G · ctrl+d/u page · space pause · t theme · ? help"
    if tab == "queue":
        return "enter jump · d remove · r/q tabs · h/l seek · n/p · " + common
    return "enter play · a queue · / search · q queue · h/l seek · n/p · " + common


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

            used = len(chip) + 7 + sum(len(text) for text, _ in rest)
            title_budget = max(8, width - used)
            out.append(_truncate(self.track.title, title_budget), style="bold")
            for text, style in rest:
                out.append(text, style=style)

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

[b]Queue[/b]
  a                    add selected result to queue
  d                    remove selected queue entry
  enter                play now / jump to entry

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


class LoginScreen(ModalScreen[bool]):
    """Device-code login: paste Google OAuth client credentials, then approve.

    Dismisses True on success, False on cancel. Runs the ytmusicapi device flow
    in workers; cancellation is cooperative via a threading.Event.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("q", "cancel", "Cancel", show=False),
        Binding("enter", "confirm", "Continue", show=False),
        Binding("o", "open_browser", "Open browser", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.stage = "creds"  # creds | code | polling
        self._cancelled = threading.Event()
        self._code: auth.DeviceCode | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="login-box"):
            yield Static(id="login-help")
            yield Input(placeholder="OAuth client ID", id="client-id")
            yield Input(placeholder="OAuth client secret", password=True, id="client-secret")
            yield Static(id="login-status")
            yield Static(id="login-hints")

    def on_mount(self) -> None:
        self.query_one("#login-help", Static).update(Text.from_markup(CREDS_HELP))
        self._set_stage("creds")
        saved = auth.load_client()
        if saved is not None:
            self.query_one("#client-id", Input).value = saved[0]
            self.query_one("#client-secret", Input).value = saved[1]
            # credentials already stored: skip straight to requesting a code
            self.action_confirm()
        else:
            self.query_one("#client-id", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "client-id":
            self.query_one("#client-secret", Input).focus()
        else:
            self.action_confirm()

    # -- state ---------------------------------------------------------------
    def _set_stage(self, stage: str) -> None:
        self.stage = stage
        show_creds = stage == "creds"
        self.query_one("#client-id", Input).display = show_creds
        self.query_one("#client-secret", Input).display = show_creds
        if show_creds:
            hints = "enter save & continue · esc cancel"
        else:
            hints = "o open browser · esc cancel"
        self.query_one("#login-hints", Static).update(Text(hints, style="dim"))
        if show_creds:
            self.query_one("#client-id", Input).focus()

    def _set_status(self, text: Text) -> None:
        self.query_one("#login-status", Static).update(text)

    # -- flow ----------------------------------------------------------------
    def action_confirm(self) -> None:
        if self.stage != "creds":
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
            self.call_from_thread(self._flow_failed, str(exc))
            return
        self.call_from_thread(self._show_code, code)

    def _show_code(self, code: auth.DeviceCode) -> None:
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
            self.call_from_thread(self._flow_failed, str(exc))
            return
        self.call_from_thread(self._login_succeeded)

    def _flow_failed(self, message: str) -> None:
        self._set_stage("creds")
        self.query_one("#login-help", Static).update(Text.from_markup(CREDS_HELP))
        self._set_status(Text(message, style="red"))

    def _login_succeeded(self) -> None:
        self.dismiss(True)

    # -- keys ----------------------------------------------------------------
    def action_cancel(self) -> None:
        self._cancelled.set()
        self.dismiss(False)

    def action_open_browser(self) -> None:
        if self._code is None:
            return
        webbrowser.open(self._code.url)
