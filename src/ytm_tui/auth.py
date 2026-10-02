"""OAuth device-code login flow and private config-file helpers.

Stores two files under the config dir (both chmod 600):
  oauth_client.json  {"client_id", "client_secret"}  pasted once by the user
  oauth.json         refreshing token written by ytmusicapi
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests
from ytmusicapi.auth.oauth import OAuthCredentials, RefreshingToken
from ytmusicapi.exceptions import YTMusicError

CONFIG_NAME = "yt-tui-player"
CLIENT_FILE = "oauth_client.json"
TOKEN_FILE = "oauth.json"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    path = Path(base) / CONFIG_NAME
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def client_path() -> Path:
    return config_dir() / CLIENT_FILE


def token_path() -> Path:
    return config_dir() / TOKEN_FILE


def _write_private(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_client() -> tuple[str, str] | None:
    try:
        data = json.loads(client_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    if isinstance(client_id, str) and isinstance(client_secret, str):
        if client_id.strip() and client_secret.strip():
            return client_id.strip(), client_secret.strip()
    return None


def save_client(client_id: str, client_secret: str) -> None:
    _write_private(
        client_path(),
        {"client_id": client_id.strip(), "client_secret": client_secret.strip()},
    )


def has_login() -> bool:
    return load_client() is not None and token_path().is_file()


@dataclass
class DeviceCode:
    verification_url: str
    user_code: str
    device_code: str
    interval: int
    expires_in: int

    @property
    def url(self) -> str:
        return f"{self.verification_url}?user_code={self.user_code}"


class PollError(RuntimeError):
    """Device flow could not complete (expired, denied, bad creds, ...)."""


class CancelledLogin(PollError):
    """User dismissed the login screen while polling."""


def begin_device_flow() -> DeviceCode:
    client = load_client()
    if client is None:
        raise PollError("OAuth client credentials are not configured")
    creds = OAuthCredentials(client[0], client[1])
    try:
        code = creds.get_code()
    except YTMusicError as exc:
        raise PollError(str(exc)) from exc
    except requests.RequestException as exc:
        raise PollError(f"Network error: {exc}") from exc
    return DeviceCode(
        verification_url=code["verification_url"],
        user_code=code["user_code"],
        device_code=code["device_code"],
        interval=max(1, int(code.get("interval", 5))),
        expires_in=int(code.get("expires_in", 600)),
    )


def poll_for_token(
    code: DeviceCode,
    *,
    should_cancel: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RefreshingToken:
    """Poll Google until the user approves on their device (or the code expires).

    Raises PollError on expiry/denial/cancellation; caller runs this in a worker.
    """
    client = load_client()
    if client is None:
        raise PollError("OAuth client credentials are not configured")
    creds = OAuthCredentials(client[0], client[1])
    deadline = time.monotonic() + code.expires_in

    while True:
        if should_cancel is not None and should_cancel():
            raise CancelledLogin("cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PollError("Login code expired before approval")
        try:
            raw = creds.token_from_code(code.device_code)
        except requests.RequestException:
            sleep(min(code.interval, remaining))
            continue
        except YTMusicError as exc:
            raise PollError(str(exc)) from exc
        if isinstance(raw, dict) and "access_token" in raw:
            refreshable = RefreshingToken(
                credentials=creds,
                access_token=raw["access_token"],
                refresh_token=raw["refresh_token"],
                scope=raw["scope"],
                token_type=raw["token_type"],
                expires_in=raw.get("refresh_token_expires_in", raw["expires_in"]),
            )
            refreshable.update(raw)
            refreshable.local_cache = token_path()
            try:
                token_path().chmod(0o600)
            except OSError:
                pass
            return refreshable
        error = raw.get("error") if isinstance(raw, dict) else None
        if error == "authorization_pending":
            sleep(min(code.interval, remaining))
            continue
        if error in ("expired_token", "access_denied"):
            raise PollError(f"Login {error.replace('_', ' ')}")
        raise PollError(f"Login failed: {error or raw}")
