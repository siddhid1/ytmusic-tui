"""Worker callbacks must go through App.call_from_thread (regression).

A browser-session submit used to raise AttributeError inside the error
handler, killing the app before the real HeadersError could be shown.
"""

from __future__ import annotations

import asyncio

from textual.widgets import TextArea

from ytm_tui import auth
from ytm_tui.app import YTMusicTUI
from ytm_tui.widgets import LoginScreen

CURL_SAMPLE = (
    "curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\\n"
    "  -H 'cookie: __Secure-3PAPISID=H; VISITOR_INFO1_LIVE=x' \\\n"
    "  -H 'x-goog-authuser: 0' \\\n"
    "  --compressed"
)


def test_headers_error_reaches_login_status(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("YT_TUI_MPV_EXTRA", "--ao=null")

    def boom() -> dict:
        raise auth.HeadersError("kaboom-test")

    monkeypatch.setattr(auth, "verify_browser_session", boom)

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("ctrl+l")
            await pilot.press("ctrl+b")
            screen = app.screen
            assert isinstance(screen, LoginScreen)
            screen.query_one("#headers-paste", TextArea).text = CURL_SAMPLE
            await pilot.press("enter")
            status = ""
            for _ in range(100):
                await pilot.pause(0.05)
                status = str(screen.query_one("#login-status").render())
                if "kaboom-test" in status:
                    break
            assert "kaboom-test" in status, f"status never showed the error: {status!r}"

    asyncio.run(scenario())
