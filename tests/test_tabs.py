"""Keys 1-5 switch the main panes; the library pane guides guests to sign in."""

from __future__ import annotations

import asyncio

from ytm_tui.app import YTMusicTUI

PANES = [
    ("3", "library"),
    ("4", "history"),
    ("5", "profile"),
    ("1", "results"),
    ("2", "queue"),
]


def test_digit_keys_switch_panes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("YT_TUI_MPV_EXTRA", "--ao=null")

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")  # leave the search input (digits type there)
            tabs = app.query_one("#tabs")
            for key, pane in PANES:
                await pilot.press(key)
                await pilot.pause()
                assert tabs.active == pane, f"press {key} → {pane} (got {tabs.active})"
            assert tabs.active == "queue"
            await pilot.press("3")
            await pilot.pause()
            assert app.focused is not None and app.focused.id == "library-table"

    asyncio.run(scenario())


def test_guest_library_shows_sign_in_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("YT_TUI_MPV_EXTRA", "--ao=null")

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")  # leave the search input (digits type there)
            await pilot.press("3")
            await pilot.pause()
            row = app.query_one("#library-table").get_row_at(0)
            assert "Sign in (ctrl+l)" in str(row[1]), f"guest hint missing: {row!r}"
            await pilot.press("5")
            await pilot.pause()
            assert app.query_one("#tabs").active == "profile"

    asyncio.run(scenario())


def test_guest_history_shows_sign_in_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("YT_TUI_MPV_EXTRA", "--ao=null")

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.press("4")
            await pilot.pause()
            assert app.query_one("#tabs").active == "history"
            row = app.query_one("#history-table").get_row_at(0)
            assert "Sign in (ctrl+l)" in str(row[1]), f"guest hint missing: {row!r}"

    asyncio.run(scenario())


def test_library_table_fills_pane(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("YT_TUI_MPV_EXTRA", "--ao=null")

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")
            await pilot.press("3")
            await pilot.pause()
            pane = app.query_one("#library")
            section_tabs = app.query_one("#lib-tabs")
            table = app.query_one("#library-table")
            assert section_tabs.size.height <= 5, f"section tabs grew: {section_tabs.size}"
            assert table.size.height >= 10, f"library table squeezed off-pane: {table.size}"
            assert section_tabs.size.height + table.size.height <= pane.size.height + 1
            await pilot.press("4")
            await pilot.pause()
            history = app.query_one("#history-table")
            assert history.size.height >= 10, f"history table squeezed: {history.size}"
            await pilot.press("5")
            await pilot.pause()
            profile = app.query_one("#profile-table")
            assert profile.size.height >= 10, f"profile table squeezed: {profile.size}"

    asyncio.run(scenario())
