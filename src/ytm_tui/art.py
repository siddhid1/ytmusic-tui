"""Album-art fetching and half-block rendering (works in any terminal/tmux)."""

from __future__ import annotations

import io
from functools import lru_cache

import requests
from rich.style import Style
from rich.text import Text

ART_TIMEOUT = 10.0
COLS = 16
ROWS = 16
USER_AGENT = "yt-tui-player"


@lru_cache(maxsize=32)
def _fetch_cached(url: str) -> bytes | None:
    try:
        response = requests.get(url, timeout=ART_TIMEOUT, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException:
        return None
    return response.content


def fetch(url: str) -> bytes | None:
    """Download image bytes (memory-cached per URL); None on failure."""
    return _fetch_cached(url)


def render_halfblock(data: bytes, cols: int = COLS, rows: int = ROWS) -> Text | None:
    """Render image bytes as ▀ cells with truecolor fg/bg; None on bad input."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # UnidentifiedImageError, truncated file, ...
        return None
    image = image.resize((cols, rows * 2), Image.Resampling.LANCZOS)
    pixels = image.load()
    out = Text()
    for y in range(rows):
        if y:
            out.append("\n")
        for x in range(cols):
            upper = pixels[x, y * 2]
            lower = pixels[x, y * 2 + 1]
            out.append(
                "▀",
                style=Style(
                    color=f"rgb({upper[0]},{upper[1]},{upper[2]})",
                    bgcolor=f"rgb({lower[0]},{lower[1]},{lower[2]})",
                ),
            )
    return out


def placeholder(cols: int = COLS, rows: int = ROWS) -> Text:
    """Dim empty square shown when nothing is playing / art is loading."""
    line = "░" * cols
    out = Text()
    for y in range(rows):
        if y:
            out.append("\n")
        out.append(line, style="dim")
    return out
