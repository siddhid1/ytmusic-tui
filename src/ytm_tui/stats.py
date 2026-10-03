"""Local play statistics: per-track listening events stored in the config dir.

An event is appended whenever a track stops playing. The Profile tab
aggregates these into minutes/plays per artist. Everything here is
best-effort — a failed read or write must never interrupt playback.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .auth import _write_private, config_dir
from .models import HistoryEntry, Track

STATS_FILE = "local_stats.json"
MAX_EVENTS = 2000  # cap so the file stays small and rewrites stay cheap
MIN_SECONDS = 2.0  # skips/noise below this are not recorded
PLAY_THRESHOLD = 30.0  # seconds before an event counts as a "play"


@dataclass(frozen=True, slots=True)
class PlayEvent:
    video_id: str
    title: str
    artists: tuple[str, ...]
    seconds: float
    ts: float  # unix time when the track stopped playing


@dataclass(frozen=True, slots=True)
class ArtistStat:
    name: str
    seconds: float
    plays: int


def stats_path() -> Path:
    return config_dir() / STATS_FILE


def load_events(path: Path | None = None) -> list[PlayEvent]:
    """Read stored events; a missing or corrupt file yields an empty list."""
    target = path or stats_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    events: list[PlayEvent] = []
    for item in data.get("events") or []:
        if not isinstance(item, dict) or not item.get("video_id"):
            continue
        try:
            events.append(
                PlayEvent(
                    video_id=str(item["video_id"]),
                    title=str(item.get("title") or ""),
                    artists=tuple(str(a) for a in item.get("artists") or []),
                    seconds=float(item.get("seconds") or 0),
                    ts=float(item.get("ts") or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return events


def record(
    track: Track,
    seconds: float,
    *,
    now: float | None = None,
    path: Path | None = None,
) -> PlayEvent | None:
    """Store one listen; returns None when it is too short to count."""
    if seconds < MIN_SECONDS:
        return None
    event = PlayEvent(
        video_id=track.video_id,
        title=track.title,
        artists=track.artists,
        seconds=round(float(seconds), 1),
        ts=now if now is not None else time.time(),
    )
    target = path or stats_path()
    events = load_events(target)
    events.append(event)
    if len(events) > MAX_EVENTS:
        events = events[-MAX_EVENTS:]
    payload = {
        "events": [
            {
                "video_id": e.video_id,
                "title": e.title,
                "artists": list(e.artists),
                "seconds": e.seconds,
                "ts": e.ts,
            }
            for e in events
        ]
    }
    _write_private(target, payload)
    return event


def artist_stats(events: list[PlayEvent]) -> list[ArtistStat]:
    """Per-artist totals (seconds, plays), most listened first.

    A track credits its full duration to every artist it lists.
    """
    totals: dict[str, float] = {}
    plays: dict[str, int] = {}
    for event in events:
        for name in event.artists or ("Unknown artist",):
            totals[name] = totals.get(name, 0.0) + event.seconds
            if event.seconds >= PLAY_THRESHOLD:
                plays[name] = plays.get(name, 0) + 1
    return [
        ArtistStat(name=name, seconds=round(seconds, 1), plays=plays.get(name, 0))
        for name, seconds in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]


@dataclass(frozen=True, slots=True)
class ProfileRow:
    """One artist row of the profile table."""

    name: str
    recent_seconds: float  # from YouTube Music history
    app_seconds: float  # from this app's local tracker
    app_plays: int


def profile_rows(
    recent: list[HistoryEntry],
    local: list[PlayEvent],
    top: int = 50,
) -> list[ProfileRow]:
    """Union of both sources for the profile table, most listened first."""
    recent_totals: dict[str, float] = {}
    for entry in recent:
        seconds = float(entry.track.duration or 0)
        if seconds <= 0:
            continue
        for name in entry.track.artists or ("Unknown artist",):
            recent_totals[name] = recent_totals.get(name, 0.0) + seconds
    app = {s.name: (s.seconds, s.plays) for s in artist_stats(local)}
    rows = []
    for name in set(recent_totals) | set(app):
        app_seconds, app_plays = app.get(name, (0.0, 0))
        rows.append(
            ProfileRow(
                name=name,
                recent_seconds=round(recent_totals.get(name, 0.0), 1),
                app_seconds=app_seconds,
                app_plays=app_plays,
            )
        )
    rows.sort(key=lambda r: r.recent_seconds + r.app_seconds, reverse=True)
    return rows[:top]


def profile_summary(
    recent: list[HistoryEntry], local: list[PlayEvent]
) -> tuple[float, int, float, int]:
    """Totals for the profile summary line: (recent_s, recent_plays, app_s, app_plays)."""
    recent_seconds = sum(float(e.track.duration or 0) for e in recent)
    app_seconds = sum(e.seconds for e in local)
    app_plays = sum(1 for e in local if e.seconds >= PLAY_THRESHOLD)
    return recent_seconds, len(recent), app_seconds, app_plays
