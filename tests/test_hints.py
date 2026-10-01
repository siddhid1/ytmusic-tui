"""Tests for statusline hint logic and progress rendering."""

from ytm_tui.widgets import _progress_bar, _truncate, hints_for


class TestHintsFor:
    def test_insert_mode(self):
        hints = hints_for("insert", "results")
        assert "esc" in hints and "normal" in hints

    def test_normal_results_has_play_and_search(self):
        hints = hints_for("normal", "results")
        assert "enter play" in hints
        assert "/ search" in hints
        assert "a queue" in hints

    def test_normal_queue_has_remove_and_jump(self):
        hints = hints_for("normal", "queue")
        assert "d remove" in hints
        assert "enter jump" in hints

    def test_common_vim_keys_present_in_normal(self):
        for tab in ("results", "queue"):
            hints = hints_for("normal", tab)
            assert "j/k" in hints
            assert "gg/G" in hints
            assert "ctrl+d" in hints
            assert "? help" in hints

    def test_queue_hints_do_not_advertise_play(self):
        assert "enter play" not in hints_for("normal", "queue")


class TestProgressBar:
    def test_empty_when_no_duration(self):
        assert _progress_bar(1.0, None) == "░" * 24

    def test_partial(self):
        bar = _progress_bar(50.0, 100.0)
        assert len(bar) == 24
        assert bar.count("█") == 12

    def test_clamped_over_full(self):
        bar = _progress_bar(200.0, 100.0)
        assert bar == "█" * 24


class TestTruncate:
    def test_short_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_long_gets_ellipsis(self):
        out = _truncate("hello world", 6)
        assert out == "hello…"
        assert len(out) == 6

    def test_zero_width(self):
        assert _truncate("hello", 0) == ""
