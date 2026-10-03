"""Profile aggregation (recent history + local stats) and the guest pane."""

from __future__ import annotations

import asyncio

from ytm_tui import stats
from ytm_tui.models import HistoryEntry, Track


def _entry(video_id: str, artists: tuple[str, ...], duration: int) -> HistoryEntry:
    return HistoryEntry(
        track=Track(video_id=video_id, title="T", artists=artists, duration=duration),
        played="Today",
    )


def test_profile_rows_union_and_sort():
    recent = [_entry("r1", ("Alpha", "Beta"), 180)]
    local = [
        stats.PlayEvent("l1", "L", ("Beta",), 60.0, 0.0),
        stats.PlayEvent("l2", "L2", ("Gamma",), 120.0, 0.0),
    ]
    rows = stats.profile_rows(recent, local)
    by_name = {r.name: r for r in rows}
    assert set(by_name) == {"Alpha", "Beta", "Gamma"}
    assert by_name["Alpha"].recent_seconds == 180
    assert by_name["Alpha"].app_seconds == 0
    assert by_name["Beta"].recent_seconds == 180
    assert by_name["Beta"].app_seconds == 60
    assert by_name["Beta"].app_plays == 1  # 60s counts as a play
    assert by_name["Gamma"].recent_seconds == 0
    # Beta 240s > Alpha 180s > Gamma 120s
    assert [r.name for r in rows] == ["Beta", "Alpha", "Gamma"]


def test_profile_rows_respect_top_limit():
    recent = [_entry(f"r{i}", (f"Artist{i}",), 100) for i in range(60)]
    assert len(stats.profile_rows(recent, [], top=50)) == 50


def test_profile_summary_totals():
    recent = [_entry("r1", ("A",), 180), _entry("r2", ("A",), 120)]
    local = [
        stats.PlayEvent("l1", "L", ("A",), 45.0, 0.0),
        stats.PlayEvent("l2", "L2", ("B",), 10.0, 0.0),
    ]
    recent_s, recent_plays, app_s, app_plays = stats.profile_summary(recent, local)
    assert recent_s == 300
    assert recent_plays == 2
    assert app_s == 55
    assert app_plays == 1  # only the 45s listen counts


def test_account_info_mapping(monkeypatch):
    from ytm_tui import ytm
    from ytm_tui.ytm import YTMusicClient

    class FakeYT:
        def get_account_info(self):
            return {
                "accountName": "Jay",
                "channelHandle": "@jay",
                "accountPhotoUrl": "https://example.invalid/photo",
            }

    monkeypatch.setattr(ytm, "_build_yt", FakeYT)
    assert YTMusicClient().account_info() == {
        "accountName": "Jay",
        "channelHandle": "@jay",
        "accountPhotoUrl": "https://example.invalid/photo",
    }


def test_guest_profile_renders_local_free(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("YT_TUI_MPV_EXTRA", "--ao=null")
    from ytm_tui.app import YTMusicTUI

    async def scenario() -> None:
        app = YTMusicTUI()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("escape")  # leave the search input (digits type there)
            await pilot.press("5")
            await pilot.pause()
            assert app.query_one("#tabs").active == "profile"
            summary = str(app.query_one("#profile-summary").render())
            assert "No listening data yet" in summary, summary
            assert "sign in (ctrl+l)" in summary, summary
            assert not app.query_one("#profile-avatar").display, "guest must not show avatar"
            row = app.query_one("#profile-table").get_row_at(0)
            assert "No listening data yet" in str(row[1]), row

    asyncio.run(scenario())
