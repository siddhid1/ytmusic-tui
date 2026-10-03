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
from .models import Track

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
