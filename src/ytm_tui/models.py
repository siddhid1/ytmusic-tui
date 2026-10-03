"""Data models for tracks and search results."""

from __future__ import annotations

from dataclasses import dataclass, field


def format_duration(seconds: int | None) -> str:
    """Format seconds as m:ss (or h:mm:ss when >= 1 hour)."""
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@dataclass(frozen=True, slots=True)
class Track:
    video_id: str
    title: str
    artists: tuple[str, ...] = ()
    album: str | None = None
    duration: int | None = None  # seconds
    thumbnail: str | None = None  # largest available cover image URL

    @property
    def artist_str(self) -> str:
        return ", ".join(self.artists) if self.artists else "Unknown artist"

    @property
    def url(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}"

    @property
    def duration_str(self) -> str:
        return format_duration(self.duration)

    @property
    def display_title(self) -> str:
        return f"{self.title} — {self.artist_str}"


@dataclass(slots=True)
class SearchResult:
    query: str
    tracks: list[Track] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Playlist:
    playlist_id: str
    title: str
    count: str | None = None  # ytmusicapi returns counts as strings ("5")


@dataclass(frozen=True, slots=True)
class LibraryAlbum:
    browse_id: str
    title: str
    artist: str = ""
    year: str = ""


@dataclass(frozen=True, slots=True)
class LibraryArtist:
    browse_id: str
    name: str
    detail: str = ""  # subscriber count when YouTube shows one


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    track: Track
    played: str = ""  # shelf label: "Today", "Yesterday", "This week", …
    feedback_token: str = ""  # YouTube's token for removing this entry
