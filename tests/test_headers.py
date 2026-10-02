"""Browser-session sign-in: header parsing, storage, and client priority."""

from __future__ import annotations

import json

import pytest
from ytmusicapi import YTMusic
from ytmusicapi.auth.types import AuthType

from ytm_tui import auth, ytm

RAW_HEADERS = "\n".join(
    [
        ":authority: music.youtube.com",
        ":method: POST",
        "cookie: __Secure-3PAPISID=HASH123; VISITOR_INFO1_LIVE=abc",
        "x-goog-authuser: 0",
        "authorization: SAPISIDHASH 1700000000:deadbeef",
        "x-goog-visitor-id: CgtIUkVFR0FMSaXNaQFI...",
        "origin: https://music.youtube.com",
        "user-agent: Mozilla/5.0",
        "host: music.youtube.com",
        "sec-fetch-mode: cors",
    ]
)

# Chrome/Brave "Copy as cURL (bash)" of a signed-in youtubei request
CURL_BASH = (
    "curl 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\\n"
    "  -H 'accept: */*' \\\n"
    "  -H 'accept-encoding: gzip, deflate, br, zstd' \\\n"
    "  -H 'authorization: SAPISIDHASH 1790965233_dc4e SAPISID1PHASH 1_dc4e' \\\n"
    "  -H 'content-type: application/json' \\\n"
    "  -H 'cookie: __Secure-3PAPISID=HASH123; VISITOR_INFO1_LIVE=abc' \\\n"
    "  -H 'origin: https://music.youtube.com' \\\n"
    "  -H 'user-agent: Mozilla/5.0' \\\n"
    "  -H 'x-goog-authuser: 0' \\\n"
    "  -H 'x-goog-visitor-id: CgtIUkVFR0FMSaXNaQFI' \\\n"
    "  --compressed"
)

# DevTools two-line format (name line, then value line) with HTTP/2 pseudo-headers
TWO_LINE_PSEUDO = "\n".join(
    [
        ":authority",
        "music.youtube.com",
        ":method",
        "POST",
        ":path",
        "/youtubei/v1/browse",
        ":scheme",
        "https",
        "cookie: __Secure-3PAPISID=HASH123; VISITOR_INFO1_LIVE=abc",
        "x-goog-authuser: 0",
        "x-goog-visitor-id: CgtIUkVFR0FMSaXNaQFI...",
    ]
)


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


class TestSaveBrowserHeaders:
    def test_roundtrip_creates_private_file(self):
        auth.save_browser_headers(RAW_HEADERS)
        path = auth.browser_path()
        assert path.is_file()
        assert path.stat().st_mode & 0o777 == 0o600
        data = json.loads(path.read_text())
        assert "__Secure-3PAPISID" in data["cookie"]
        assert data["x-goog-authuser"] == "0"
        assert "SAPISIDHASH" in data["authorization"]
        assert "host" not in data
        assert not any(key.startswith("sec") for key in data)

    def test_missing_cookie_raises_and_stores_nothing(self):
        raw = "x-goog-authuser: 0\nauthorization: SAPISIDHASH 1:a\n"
        with pytest.raises(auth.HeadersError, match="cookie"):
            auth.save_browser_headers(raw)
        assert not auth.browser_path().exists()

    def test_missing_authorization_gets_placeholder(self):
        raw = (
            "cookie: __Secure-3PAPISID=H; VISITOR_INFO1_LIVE=x\n"
            "x-goog-authuser: 0\n"
            "x-goog-visitor-id: CgtIUkVFR0FMSaXNaQFI...\n"
        )
        auth.save_browser_headers(raw)
        data = json.loads(auth.browser_path().read_text())
        assert data["authorization"].startswith("SAPISIDHASH")

    def test_cookie_without_sapisid_rejected(self):
        raw = (
            "cookie: VISITOR_INFO1_LIVE=x\n"
            "x-goog-authuser: 0\n"
            "x-goog-visitor-id: CgtIUkVFR0FMSaXNaQFI...\n"
        )
        with pytest.raises(auth.HeadersError, match="3PAPISID"):
            auth.save_browser_headers(raw)
        assert not auth.browser_path().exists()

    def test_failed_reimport_keeps_previous_headers(self):
        auth.save_browser_headers(RAW_HEADERS)
        with pytest.raises(auth.HeadersError):
            auth.save_browser_headers("nonsense without colons??")
        assert auth.browser_path().is_file()


class TestClientConstruction:
    def test_detects_browser_auth_type(self):
        auth.save_browser_headers(RAW_HEADERS)
        client = YTMusic(auth=str(auth.browser_path()))
        assert client.auth_type == AuthType.BROWSER
        assert client.sapisid == "HASH123"


class TestBuildPriority:
    def test_guest_when_no_credentials(self):
        assert ytm._build_yt().auth_type == AuthType.UNAUTHORIZED

    def test_browser_headers_used(self):
        auth.save_browser_headers(RAW_HEADERS)
        assert ytm._build_yt().auth_type == AuthType.BROWSER

    def test_oauth_wins_over_browser(self):
        auth.save_client("cid", "csec")
        auth.token_path().write_text(
            json.dumps(
                {
                    "scope": "youtube",
                    "token_type": "Bearer",
                    "access_token": "at",
                    "refresh_token": "rt",
                    "expires_at": 4102444800,
                    "expires_in": 3600,
                }
            ),
            encoding="utf-8",
        )
        auth.save_browser_headers(RAW_HEADERS)
        assert ytm._build_yt().auth_type == AuthType.OAUTH_CUSTOM_CLIENT


class TestCurlPaste:
    def test_curl_bash_parses_and_saves(self):
        auth.save_browser_headers(CURL_BASH)
        path = auth.browser_path()
        assert path.stat().st_mode & 0o777 == 0o600
        data = json.loads(path.read_text())
        assert "__Secure-3PAPISID" in data["cookie"]
        assert data["x-goog-authuser"] == "0"
        assert data["authorization"].startswith("SAPISIDHASH")
        assert not any(key.lower() == "accept-encoding" for key in data)
        assert YTMusic(auth=str(path)).auth_type == AuthType.BROWSER

    def test_curl_cmd_quoting_rejected(self):
        raw = (
            'curl ^"https://music.youtube.com/^" '
            '-H ^"cookie: __Secure-3PAPISID=HASH123^" '
            '-H ^"x-goog-authuser: 0^"'
        )
        with pytest.raises(auth.HeadersError, match="cURL"):
            auth.save_browser_headers(raw)
        assert not auth.browser_path().exists()

    def test_curl_without_headers_errors(self):
        raw = "curl 'https://music.youtube.com/' \\\n  --compressed"
        with pytest.raises(auth.HeadersError, match="No -H headers"):
            auth.save_browser_headers(raw)

    def test_non_curl_input_untouched(self):
        assert auth._curl_to_header_lines(RAW_HEADERS) == RAW_HEADERS


class TestSanitization:
    def test_two_line_pseudo_headers_leave_no_junk(self):
        auth.save_browser_headers(TWO_LINE_PSEUDO)
        data = json.loads(auth.browser_path().read_text())
        assert not any(key.startswith(":") for key in data)
        # pseudo-header values must not leak in as header names
        assert "music.youtube.com" not in data
        assert "/youtubei/v1/browse" not in data
        assert "POST" not in data
        assert "https" not in data
        assert data["x-goog-authuser"] == "0"

    def test_mixed_case_transport_headers_dropped(self):
        raw = "\n".join(
            [
                "Accept-Encoding",
                "gzip, deflate, br, zstd",
                "Content-Length",
                "1860",
                "Sec-Fetch-Mode",
                "cors",
                "Host",
                "music.youtube.com",
                "cookie: __Secure-3PAPISID=HASH123; VISITOR_INFO1_LIVE=abc",
                "x-goog-authuser: 0",
                "x-goog-visitor-id: CgtIUkVFR0FMSaXNaQFI...",
            ]
        )
        auth.save_browser_headers(raw)
        data = json.loads(auth.browser_path().read_text())
        for junk in ("accept-encoding", "content-length", "host"):
            assert not any(key.lower() == junk for key in data)
        assert not any(key.lower().startswith("sec") for key in data)
        assert data["x-goog-authuser"] == "0"
