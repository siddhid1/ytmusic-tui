"""Tests for OAuth config helpers and the device-flow poll loop (no network)."""

from __future__ import annotations

import json

import pytest

from ytm_tui import auth


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


class TestConfig:
    def test_config_dir_under_xdg(self, tmp_path):
        assert auth.config_dir() == tmp_path / "yt-tui-player"
        assert auth.config_dir().is_dir()

    def test_save_and_load_client_roundtrip(self):
        assert auth.load_client() is None
        auth.save_client("  my-id  ", " my-secret ")
        assert auth.load_client() == ("my-id", "my-secret")

    def test_client_file_is_private(self):
        auth.save_client("id", "secret")
        assert auth.client_path().stat().st_mode & 0o777 == 0o600

    def test_malformed_client_json_is_none(self):
        auth.client_path().write_text("{not json", encoding="utf-8")
        assert auth.load_client() is None

    def test_empty_client_values_are_none(self):
        auth.client_path().write_text(
            json.dumps({"client_id": "  ", "client_secret": "s"}), encoding="utf-8"
        )
        assert auth.load_client() is None

    def test_has_login_needs_both_files(self):
        assert not auth.has_login()
        auth.save_client("id", "secret")
        assert not auth.has_login()
        auth.token_path().write_text("{}", encoding="utf-8")
        assert auth.has_login()


class TestDeviceCode:
    def test_url_contains_user_code(self):
        code = auth.DeviceCode(
            verification_url="https://www.youtube.com/activate",
            user_code="ABCD-1234",
            device_code="dev",
            interval=5,
            expires_in=600,
        )
        assert code.url == "https://www.youtube.com/activate?user_code=ABCD-1234"


class FakeCreds:
    """Scripted OAuthCredentials: N pending responses, then success/failure."""

    responses: list = []
    seen: list = []

    def __init__(self, client_id, client_secret, *args, **kwargs):
        self.client_id = client_id
        self.client_secret = client_secret

    def get_code(self):
        return {
            "verification_url": "https://example.test/activate",
            "user_code": "XYZ-999",
            "device_code": "devcode",
            "interval": 1,
            "expires_in": 600,
        }

    def token_from_code(self, device_code):
        FakeCreds.seen.append(device_code)
        return FakeCreds.responses.pop(0)


GOOD_TOKEN = {
    "access_token": "at-1",
    "refresh_token": "rt-1",
    "scope": "scope",
    "token_type": "Bearer",
    "expires_in": 3600,
}


@pytest.fixture
def fake_creds(monkeypatch):
    FakeCreds.responses = []
    FakeCreds.seen = []
    monkeypatch.setattr(auth, "OAuthCredentials", FakeCreds)
    auth.save_client("cid", "csec")
    return FakeCreds


class TestPollForToken:
    def test_pending_then_success_stores_token(self, fake_creds, tmp_path):
        fake_creds.responses = [
            {"error": "authorization_pending"},
            {"error": "authorization_pending"},
            dict(GOOD_TOKEN),
        ]
        code = auth.begin_device_flow()
        token = auth.poll_for_token(code, sleep=lambda _s: None)
        assert token.access_token == "at-1"
        assert fake_creds.seen == ["devcode", "devcode", "devcode"]
        stored = json.loads(auth.token_path().read_text())
        assert stored["refresh_token"] == "rt-1"
        assert auth.token_path().stat().st_mode & 0o777 == 0o600

    def test_expired_code_raises(self, fake_creds):
        code = auth.DeviceCode("u", "c", "d", interval=1, expires_in=0)
        with pytest.raises(auth.PollError, match="expired"):
            auth.poll_for_token(code, sleep=lambda _s: None)

    def test_access_denied_raises(self, fake_creds):
        fake_creds.responses = [{"error": "access_denied"}]
        code = auth.begin_device_flow()
        with pytest.raises(auth.PollError, match="access denied"):
            auth.poll_for_token(code, sleep=lambda _s: None)

    def test_cancel_raises_cancelled(self, fake_creds):
        code = auth.begin_device_flow()
        with pytest.raises(auth.CancelledLogin):
            auth.poll_for_token(code, should_cancel=lambda: True, sleep=lambda _s: None)

    def test_slow_down_bumps_interval_then_succeeds(self, fake_creds):
        fake_creds.responses = [
            {"error": "slow_down"},
            {"error": "authorization_pending"},
            dict(GOOD_TOKEN),
        ]
        code = auth.begin_device_flow()
        sleeps: list[float] = []
        token = auth.poll_for_token(code, sleep=sleeps.append)
        assert token.access_token == "at-1"
        assert sleeps == [6, 6]  # interval 1 -> 6 after slow_down, then reused

    def test_missing_refresh_token_raises(self, fake_creds):
        raw = dict(GOOD_TOKEN)
        del raw["refresh_token"]
        fake_creds.responses = [raw]
        code = auth.begin_device_flow()
        with pytest.raises(auth.PollError, match="refresh token"):
            auth.poll_for_token(code, sleep=lambda _s: None)

    def test_missing_client_raises(self, monkeypatch):
        monkeypatch.setattr(auth, "OAuthCredentials", FakeCreds)
        with pytest.raises(auth.PollError, match="not configured"):
            auth.begin_device_flow()
