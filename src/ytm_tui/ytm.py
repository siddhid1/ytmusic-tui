"""Thin wrapper around ytmusicapi for the search operations used by the TUI."""

from __future__ import annotations

import threading

from ytmusicapi import YTMusic
from ytmusicapi.auth.oauth import OAuthCredentials
from ytmusicapi.auth.types import AuthType

from . import auth
from .models import Playlist, Track

_MAX_RESULTS = 25


def _build_yt() -> YTMusic:
    """Guest client, or an authenticated one when oauth files are present."""
    client = auth.load_client()
    if auth.has_login() and client is not None:
        return YTMusic(
            auth=str(auth.token_path()),
            oauth_credentials=OAuthCredentials(client[0], client[1]),
        )
    return YTMusic()


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
        self._yt = _build_yt()
        self._lock = threading.Lock()

    @property
    def authed(self) -> bool:
        return self._yt.auth_type != AuthType.UNAUTHORIZED

    def enable_auth(self) -> bool:
        """Rebuild the underlying client to pick up a freshly stored oauth token."""
        with self._lock:
            try:
                self._yt = _build_yt()
            except Exception:
                return False
        return self.authed

    def search_songs(self, query: str, limit: int = _MAX_RESULTS) -> list[Track]:
        with self._lock:
            raw = self._yt.search(query, filter="songs", limit=limit)
        tracks = []
        for item in raw or []:
            track = _to_track(item)
            if track:
                tracks.append(track)
        return tracks

    def list_playlists(self, limit: int | None = None) -> list[Playlist]:
        """The user's saved playlists (requires login; limit=None retrieves all)."""
        with self._lock:
            raw = self._yt.get_library_playlists(limit=limit)
        playlists = []
        for item in raw or []:
            playlist_id = item.get("playlistId")
            title = item.get("title")
            if playlist_id and title:
                count = item.get("count")
                playlists.append(
                    Playlist(
                        playlist_id=playlist_id,
                        title=title,
                        count=str(count) if count is not None else None,
                    )
                )
        return playlists

    def add_to_playlist(self, playlist_id: str, video_ids: list[str]) -> None:
        """Append songs to a playlist; raise RuntimeError when YT reports failure."""
        if not video_ids:
            return
        with self._lock:
            result = self._yt.add_playlist_items(playlist_id, list(video_ids))
        status = result.get("status", "") if isinstance(result, dict) else str(result)
        if "SUCCEEDED" not in status:
            raise RuntimeError(f"add failed ({status or 'no status'})")
