"""Tests for album-art URL handling, thumbnail capture, and half-block rendering."""

from __future__ import annotations

import io

import pytest

from ytm_tui import art
from ytm_tui.ytm import _best_thumbnail, art_url


class TestArtUrl:
    def test_rewrite_square_size(self):
        url = "https://yt3.googleusercontent.com/abc=w60-h60-l90-rj"
        assert art_url(url, 320) == "https://yt3.googleusercontent.com/abc=w320-h320-l90-rj"

    def test_only_first_size_rewritten(self):
        url = "https://x.test/a=w10-h10/w20-h20"
        assert art_url(url, 96) == "https://x.test/a=w96-h96/w20-h20"

    def test_unmatched_url_unchanged(self):
        assert art_url("https://x.test/plain.jpg", 320) == "https://x.test/plain.jpg"


class TestBestThumbnail:
    def test_largest_width_wins(self):
        item = {
            "thumbnails": [
                {"url": "https://x/small", "width": 60, "height": 60},
                {"url": "https://x/big", "width": 544, "height": 544},
                {"url": "https://x/mid", "width": 120, "height": 120},
            ]
        }
        assert _best_thumbnail(item) == "https://x/big"

    def test_missing_or_bad_input(self):
        assert _best_thumbnail({}) is None
        assert _best_thumbnail({"thumbnails": None}) is None
        assert _best_thumbnail({"thumbnails": []}) is None
        assert _best_thumbnail({"thumbnails": [{"width": 60}]}) is None
        assert _best_thumbnail({"thumbnails": ["nope"]}) is None

    def test_search_track_carries_thumbnail(self):
        from ytm_tui.ytm import _to_track

        track = _to_track(
            {
                "videoId": "abc",
                "title": "Song",
                "artists": [{"name": "Artist"}],
                "duration": "3:00",
                "thumbnails": [{"url": "https://x/w120-h120", "width": 120}],
            }
        )
        assert track is not None
        assert track.thumbnail == "https://x/w120-h120"


def _png_bytes(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


class TestHalfblock:
    def test_dimensions_and_glyphs(self):
        data = _png_bytes(8, 8, (255, 0, 0))
        rendered = art.render_halfblock(data, cols=4, rows=3)
        assert rendered is not None
        lines = rendered.split("\n")
        assert len(lines) == 3
        for line in lines:
            assert line.plain == "▀▀▀▀"
            assert len(line.spans) == 4  # one style span per cell

    def test_cell_colors_come_from_pixels(self):
        rendered = art.render_halfblock(_png_bytes(4, 4, (10, 20, 30)), cols=2, rows=1)
        assert rendered is not None
        span = rendered.spans[0]
        assert span.style.color is not None
        triplet = span.style.color.get_truecolor()
        assert (triplet.red, triplet.green, triplet.blue) == (10, 20, 30)

    def test_invalid_bytes_return_none(self):
        assert art.render_halfblock(b"definitely not an image") is None

    def test_placeholder_shape(self):
        text = art.placeholder(cols=5, rows=2)
        lines = text.split("\n")
        assert [line.plain for line in lines] == ["░░░░░", "░░░░░"]


class TestFetch:
    def test_fetch_uses_injected_response(self, monkeypatch):
        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                pass

            @property
            def content(self):
                return b"imagedata"

        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

        monkeypatch.setattr(art.requests, "get", fake_get)
        assert art.fetch("https://img.test/unique-1.png") == b"imagedata"
        assert captured["url"] == "https://img.test/unique-1.png"
        assert captured["timeout"] == art.ART_TIMEOUT

    def test_fetch_failure_returns_none(self, monkeypatch):
        import requests

        def boom(url, **kwargs):
            raise requests.ConnectionError("nope")

        monkeypatch.setattr(art.requests, "get", boom)
        assert art.fetch("https://img.test/unique-2.png") is None

    @pytest.mark.parametrize("cols,rows", [(16, 16), (8, 8)])
    def test_render_matches_requested_grid(self, cols, rows):
        rendered = art.render_halfblock(_png_bytes(32, 32, (0, 128, 255)), cols, rows)
        assert rendered is not None
        assert len(rendered.split("\n")) == rows
        assert len(rendered.split("\n")[0].plain) == cols
