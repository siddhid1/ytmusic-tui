"""Library sections paint per fetch; the viewed section is fetched first."""

from __future__ import annotations

import asyncio
import time

from ytm_tui.app import YTMusicTUI
from ytm_tui.models import Playlist
from ytm_tui.ytm import YTMusicClient


def _setup(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("YT_TUI_MPV_EXTRA", "--ao=null")
    monkeypatch.setattr(YTMusicClient, "authed", property(lambda self: True))


def test_section_callback_renders_while_others_still_loading(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(YTMusicTUI, "_fetch_library", lambda self, sections, token: None)

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.press("3")
            await pilot.pause()
            assert app._lib_state["playlists"] == "loading"
            playlist = Playlist(playlist_id="PL1", title="Road Trip", count="5")
            app._library_section_loaded(app._lib_token, "playlists", [playlist], "")
            await pilot.pause()
            assert app._lib_state["playlists"] == "ready"
            assert app._lib_state["albums"] == "loading"
            assert app._lib_state["artists"] == "loading"
            table = app.query_one("#library-table")
            assert table.row_count == 1, f"section did not paint: {table.row_count}"
            assert "Road Trip" in str(table.get_row_at(0)[0])
            app._library_section_loaded(app._lib_token + 7, "albums", [], "")
            await pilot.pause()
            assert app._lib_state["albums"] == "loading", "stale token was accepted"

    asyncio.run(scenario())


def test_active_section_fetched_first(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            order: list[str] = []

            def record(name: str):
                def fetch():
                    order.append(name)
                    return []

                return fetch

            app.ytm.list_playlists = record("playlists")
            app.ytm.library_albums = record("albums")
            app.ytm.library_artists = record("artists")
            app._lib_section = "artists"
            await pilot.press("escape")
            await pilot.press("3")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and set(app._lib_state.values()) != {"ready"}:
                await pilot.pause(0.1)
            assert set(app._lib_state.values()) == {"ready"}, app._lib_state
            assert order[0] == "artists", order
            assert set(order) == {"playlists", "albums", "artists"}, order

    asyncio.run(scenario())
