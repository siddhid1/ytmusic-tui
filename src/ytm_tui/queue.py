"""Play queue model — pure logic, no I/O."""

from __future__ import annotations

from .models import Track


class QueueModel:
    """Ordered list of tracks with a cursor pointing at the current item.

    The TUI renders `items`; playback controls mutate the cursor. Removing the
    current item stops playback of it (caller decides to load the new current).
    """

    def __init__(self) -> None:
        self._items: list[Track] = []
        self._cursor: int = -1  # index of playing track, -1 when idle/stopped

    # -- inspection ---------------------------------------------------------
    @property
    def items(self) -> list[Track]:
        return list(self._items)

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def current(self) -> Track | None:
        if 0 <= self._cursor < len(self._items):
            return self._items[self._cursor]
        return None

    @property
    def is_empty(self) -> bool:
        return not self._items

    def __len__(self) -> int:
        return len(self._items)

    def upcoming(self) -> list[Track]:
        """Tracks after the current one."""
        if self._cursor < 0:
            return self.items
        return self.items[self._cursor + 1 :]

    # -- mutation -----------------------------------------------------------
    def enqueue(self, track: Track) -> int:
        """Append to the end; returns the new index. Starts playback when idle."""
        self._items.append(track)
        if self._cursor < 0:
            self._cursor = len(self._items) - 1
        return len(self._items) - 1

    def play_next(self, track: Track) -> int:
        """Insert right after the current track (or at front when idle)."""
        insert_at = (self._cursor + 1) if self._cursor >= 0 else 0
        self._items.insert(insert_at, track)
        if self._cursor < 0:
            self._cursor = 0
        return insert_at

    def remove(self, index: int) -> Track | None:
        """Remove by index; adjusts cursor so the current track stays current."""
        if not 0 <= index < len(self._items):
            return None
        removed = self._items.pop(index)
        if index < self._cursor:
            self._cursor -= 1
        elif index == self._cursor:
            # keep cursor on the track now occupying this slot; -1 if we
            # removed the last item
            if self._cursor >= len(self._items):
                self._cursor = len(self._items) - 1
        if not self._items:
            self._cursor = -1
        return removed

    def next(self) -> Track | None:
        """Advance cursor to the following track (None at end)."""
        if self._cursor + 1 < len(self._items):
            self._cursor += 1
            return self.current
        return None

    def prev(self) -> Track | None:
        if self._cursor > 0:
            self._cursor -= 1
            return self.current
        return None

    def select(self, index: int) -> Track | None:
        """Jump the cursor to an existing index."""
        if 0 <= index < len(self._items):
            self._cursor = index
            return self._items[index]
        return None

    def move(self, index: int, offset: int) -> None:
        """Move item at `index` by `offset` positions (queue reordering)."""
        if not 0 <= index < len(self._items):
            return
        target = max(0, min(len(self._items) - 1, index + offset))
        if target == index:
            return
        item = self._items.pop(index)
        self._items.insert(target, item)
        # keep cursor on the same track
        if index == self._cursor:
            self._cursor = target
        elif index < self._cursor <= target:
            self._cursor -= 1
        elif target <= self._cursor < index:
            self._cursor += 1

    def clear(self) -> None:
        self._items.clear()
        self._cursor = -1
