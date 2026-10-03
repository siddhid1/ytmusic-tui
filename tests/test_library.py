"""Library adapter methods map ytmusicapi payloads onto models (no network)."""

from __future__ import annotations

from ytm_tui import ytm
from ytm_tui.models import LibraryAlbum, LibraryArtist, Track
from ytm_tui.ytm import YTMusicClient

PLAYLISTS = [{"playlistId": "PL1", "title": "Road Trip", "count": "5"}]

ALBUMS = [
    {
        "browseId": "MPRE1",
        "title": "Discovery",
        "artists": [{"name": "Daft Punk"}],
        "year": "2001",
    },
    {"title": "missing id"},  # skipped
    {"browseId": "MPRE2", "title": "Syro"},  # no artists / year
]

ARTISTS = [
    {"browseId": "UC1", "artist": "Daft Punk", "subscribers": "2.1M"},
    {"browseId": "UC2", "artist": "Boards of Canada"},
    {"artist": "missing id"},  # skipped
]

PLAYLIST_SONGS = {
    "tracks": [
        {
            "videoId": "v1",
            "title": "One More Time",
            "artists": [{"name": "Daft Punk"}],
            "album": {"name": "Discovery"},
            "duration": "5:20",
            "duration_seconds": 320,
        },
        {"title": "no video id"},  # skipped
    ]
}


class FakeYT:
    def get_library_playlists(self, limit=None):
        return PLAYLISTS

    def get_library_albums(self, limit=None):
        return ALBUMS

    def get_library_artists(self, limit=None):
        return ARTISTS

    def get_playlist(self, playlistId, limit=None):
        assert playlistId == "PL1"
        assert limit is None
        return PLAYLIST_SONGS


def _client(monkeypatch) -> YTMusicClient:
    monkeypatch.setattr(ytm, "_build_yt", FakeYT)
    return YTMusicClient()


def test_library_albums_mapping(monkeypatch):
    albums = _client(monkeypatch).library_albums()
    assert all(isinstance(a, LibraryAlbum) for a in albums)
    assert [a.title for a in albums] == ["Discovery", "Syro"]
    assert albums[0].browse_id == "MPRE1"
    assert albums[0].artist == "Daft Punk"
    assert albums[0].year == "2001"
    assert albums[1].artist == ""
    assert albums[1].year == ""


def test_library_artists_mapping(monkeypatch):
    artists = _client(monkeypatch).library_artists()
    assert all(isinstance(a, LibraryArtist) for a in artists)
    assert [a.name for a in artists] == ["Daft Punk", "Boards of Canada"]
    assert artists[0].browse_id == "UC1"
    assert artists[0].detail == "2.1M"
    assert artists[1].detail == ""


def test_playlist_tracks_mapping(monkeypatch):
    tracks = _client(monkeypatch).playlist_tracks("PL1")
    assert len(tracks) == 1
    track = tracks[0]
    assert isinstance(track, Track)
    assert track.video_id == "v1"
    assert track.title == "One More Time"
    assert track.artist_str == "Daft Punk"
    assert track.album == "Discovery"
    assert track.duration == 320
    assert track.duration_str == "5:20"


def test_playlist_tracks_error_propagates(monkeypatch):
    class Boom:
        def get_playlist(self, playlistId, limit=None):
            raise RuntimeError("404 private")

    monkeypatch.setattr(ytm, "_build_yt", Boom)
    try:
        YTMusicClient().playlist_tracks("PL1")
    except RuntimeError as exc:
        assert "404 private" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
