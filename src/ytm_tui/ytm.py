"""Thin wrapper around ytmusicapi for the search operations used by the TUI."""

from __future__ import annotations

import threading

from ytmusicapi import YTMusic

from .models import Track

_MAX_RESULTS = 25


def _parse_duration(raw: object) -> int | None:
    """Parse '3:45' / '1:02:03' into seconds; return None on anything unexpected."""
    if not isinstance(raw, str) or not raw:
        return None
    parts = raw.split(":")
    if len(parts) > 3 or not all(p.isdigit() for p in parts):
        return None
    try:
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + int(part)
    except ValueError:
        return None
    return seconds


def _to_track(item: dict) -> Track | None:
    video_id = item.get("videoId")
    if not video_id:
        return None
    artists = tuple(a.get("name", "") for a in item.get("artists") or [] if a.get("name"))
    album = None
    album_info = item.get("album")
    if isinstance(album_info, dict):
        album = album_info.get("name")
    return Track(
        video_id=video_id,
        title=item.get("title") or "Unknown title",
        artists=artists,
        album=album,
        duration=_parse_duration(item.get("duration")),
    )


class YTMusicClient:
    """Thread-safe thin client: ytmusicapi is not documented as thread-safe, so we
    serialize calls with a lock (searches run in a Textual worker thread)."""

    def __init__(self) -> None:
        self._yt = YTMusic()
        self._lock = threading.Lock()

    def search_songs(self, query: str, limit: int = _MAX_RESULTS) -> list[Track]:
        with self._lock:
            raw = self._yt.search(query, filter="songs", limit=limit)
        tracks = []
        for item in raw or []:
            track = _to_track(item)
            if track:
                tracks.append(track)
        return tracks
