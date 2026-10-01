"""Tests for the play queue model."""

from ytm_tui.models import Track
from ytm_tui.queue import QueueModel


def t(video_id: str, title: str = "Song") -> Track:
    return Track(video_id=video_id, title=title, artists=("Artist",), duration=100)


def test_empty_queue():
    q = QueueModel()
    assert q.is_empty
    assert len(q) == 0
    assert q.cursor == -1
    assert q.current is None
    assert q.next() is None
    assert q.prev() is None


def test_enqueue_starts_playback_when_idle():
    q = QueueModel()
    q.enqueue(t("a"))
    assert q.cursor == 0
    assert q.current.video_id == "a"
    q.enqueue(t("b"))
    assert q.cursor == 0  # stays on current
    assert [x.video_id for x in q.upcoming()] == ["b"]


def test_play_next_inserts_after_current():
    q = QueueModel()
    q.enqueue(t("a"))
    q.enqueue(t("b"))
    q.enqueue(t("c"))
    idx = q.play_next(t("x"))
    assert idx == 1
    assert [x.video_id for x in q.items] == ["a", "x", "b", "c"]
    assert q.cursor == 0


def test_play_next_when_idle_goes_first():
    q = QueueModel()
    q.play_next(t("x"))
    assert q.cursor == 0
    assert q.current.video_id == "x"


def test_next_and_prev():
    q = QueueModel()
    for i in "abc":
        q.enqueue(t(i))
    assert q.next().video_id == "b"
    assert q.next().video_id == "c"
    assert q.next() is None  # end of queue
    assert q.cursor == 2
    assert q.prev().video_id == "b"
    assert q.prev().video_id == "a"
    assert q.prev() is None
    assert q.cursor == 0


def test_remove_before_current_shifts_cursor():
    q = QueueModel()
    for i in "abcd":
        q.enqueue(t(i))
    q.select(2)  # current = c
    removed = q.remove(0)
    assert removed.video_id == "a"
    assert q.cursor == 1
    assert q.current.video_id == "c"


def test_remove_current_keeps_playing_next_slot():
    q = QueueModel()
    for i in "abc":
        q.enqueue(t(i))
    q.select(1)  # current = b
    q.remove(1)
    assert [x.video_id for x in q.items] == ["a", "c"]
    assert q.current.video_id == "c"  # cursor now points at successor


def test_remove_last_current_falls_back():
    q = QueueModel()
    q.enqueue(t("a"))
    q.enqueue(t("b"))
    q.select(1)
    q.remove(1)
    assert q.current.video_id == "a"
    q.remove(0)
    assert q.is_empty
    assert q.cursor == -1
    assert q.remove(0) is None


def test_remove_out_of_range():
    q = QueueModel()
    q.enqueue(t("a"))
    assert q.remove(5) is None
    assert q.remove(-1) is None
    assert len(q) == 1


def test_select():
    q = QueueModel()
    for i in "abc":
        q.enqueue(t(i))
    assert q.select(2).video_id == "c"
    assert q.cursor == 2
    assert q.select(99) is None


def test_move_keeps_cursor_on_track():
    q = QueueModel()
    for i in "abcd":
        q.enqueue(t(i))
    q.select(1)  # current = b
    q.move(1, 2)
    assert [x.video_id for x in q.items] == ["a", "c", "d", "b"]
    assert q.current.video_id == "b"
    assert q.cursor == 3


def test_move_other_item_past_cursor():
    q = QueueModel()
    for i in "abc":
        q.enqueue(t(i))
    q.select(1)  # current = b
    q.move(2, -2)  # move c to front
    assert [x.video_id for x in q.items] == ["c", "a", "b"]
    assert q.current.video_id == "b"
    assert q.cursor == 2


def test_move_clamps_to_bounds():
    q = QueueModel()
    for i in "ab":
        q.enqueue(t(i))
    q.move(0, -5)
    assert [x.video_id for x in q.items] == ["a", "b"]
    q.move(1, 5)
    assert [x.video_id for x in q.items] == ["a", "b"]


def test_clear():
    q = QueueModel()
    q.enqueue(t("a"))
    q.clear()
    assert q.is_empty and q.cursor == -1
