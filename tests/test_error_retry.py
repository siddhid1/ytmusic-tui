"""Errored library/history sections retry after a cooldown instead of waiting for staleness."""

from __future__ import annotations

import asyncio
import time

from ytm_tui.app import LIBRARY_SECTIONS, YTMusicTUI
from ytm_tui.ytm import YTMusicClient


def _setup(monkeypatch, tmp_path) -> list:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("YT_TUI_MPV_EXTRA", "--ao=null")
    monkeypatch.setattr(YTMusicClient, "authed", property(lambda self: True))
    calls: list = []
    return calls


def test_library_error_not_retried_within_cooldown(tmp_path, monkeypatch):
    calls = _setup(monkeypatch, tmp_path)

    def record(self, sections, token):
        calls.append(list(sections))

    monkeypatch.setattr(YTMusicTUI, "_fetch_library", record)

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            now = time.monotonic()
            for section in LIBRARY_SECTIONS:
                app._lib_state[section] = "ready"
            app._lib_fetched_at = now
            app._lib_state["albums"] = "error"
            app._lib_errors["albums"] = "boom"
            app._lib_error_at["albums"] = now
            await pilot.press("escape")
            await pilot.press("3")
            await pilot.pause()
            assert calls == [], f"fresh error retried: {calls}"
            app._lib_error_at["albums"] = now - 31
            await pilot.press("1")
            await pilot.press("3")
            await pilot.pause()
            assert calls == [["albums"]], f"stale error not retried: {calls}"

    asyncio.run(scenario())


def test_library_loading_sections_are_not_refetched(tmp_path, monkeypatch):
    calls = _setup(monkeypatch, tmp_path)

    def record(self, sections, token):
        calls.append(list(sections))

    monkeypatch.setattr(YTMusicTUI, "_fetch_library", record)

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            for section in LIBRARY_SECTIONS:
                app._lib_state[section] = "loading"
            app._lib_fetched_at = time.monotonic()
            await pilot.press("escape")
            await pilot.press("3")
            await pilot.pause()
            assert calls == [], f"in-flight sections refetched: {calls}"

    asyncio.run(scenario())


def test_history_error_retried_after_cooldown(tmp_path, monkeypatch):
    calls = _setup(monkeypatch, tmp_path)

    def record(self, token):
        calls.append(token)

    monkeypatch.setattr(YTMusicTUI, "_fetch_history", record)

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            now = time.monotonic()
            app._history_state = "error"
            app._history_error = "boom"
            app._history_error_at = now
            app._history_fetched_at = now
            await pilot.press("escape")
            await pilot.press("4")
            await pilot.pause()
            assert calls == [], f"fresh history error retried: {calls}"
            app._history_error_at = now - 31
            await pilot.press("1")
            await pilot.press("4")
            await pilot.pause()
            assert len(calls) == 1, f"stale history error not retried: {calls}"

    asyncio.run(scenario())
