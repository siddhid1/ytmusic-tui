"""OAuth device-code login flow and private config-file helpers.

Stores two files under the config dir (both chmod 600):
  oauth_client.json  {"client_id", "client_secret"}  pasted once by the user
  oauth.json         refreshing token written by ytmusicapi
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests
from ytmusicapi import YTMusic
from ytmusicapi.auth.oauth import OAuthCredentials, RefreshingToken
from ytmusicapi.exceptions import YTMusicError

CONFIG_NAME = "yt-tui-player"
CLIENT_FILE = "oauth_client.json"
TOKEN_FILE = "oauth.json"
BROWSER_FILE = "browser_headers.json"


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


# -- browser-session sign-in -------------------------------------------------


class HeadersError(RuntimeError):
    """Pasted browser headers could not be parsed or are no longer valid."""


def browser_path() -> Path:
    return config_dir() / BROWSER_FILE


def has_browser_login() -> bool:
    return browser_path().is_file()


_CURL_START = re.compile(r"^\s*curl(?:\.exe)?\b", re.IGNORECASE)
_CURL_HEADER = re.compile(r"""(?<![\w-])(?:-H|--header)\s+(?:'((?:[^']|'\\'')*)'|"([^"]*)")""")
_CMD_ESCAPED_QUOTE = re.compile(r"(?:\^|`)[\"']")
_DROP_EXACT = {"host", "content-length", "accept-encoding"}


def _curl_to_header_lines(raw: str) -> str:
    """Convert Chrome/Brave "Copy as cURL (bash)" output to header lines.

    Plain header pastes pass through untouched; anything we cannot parse
    raises HeadersError with instructions instead of failing opaquely later.
    """
    if not _CURL_START.match(raw):
        return raw
    pairs = []
    for match in _CURL_HEADER.finditer(raw):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        if value is None:
            continue
        value = value.replace("'\\''", "'")
        name, sep, rest = value.partition(":")
        if not sep:
            continue
        pairs.append(f"{name.strip()}: {rest.strip()}")
    if not pairs:
        if _CMD_ESCAPED_QUOTE.search(raw):
            raise HeadersError(
                "cURL (cmd/PowerShell) quoting is not supported — use Copy as cURL (bash)"
            )
        raise HeadersError(
            "No -H headers found — copy a signed-in youtubei/v1 request with Copy as cURL (bash)"
        )
    return "\n".join(pairs)


def _strip_pseudo_headers(raw: str) -> str:
    """Drop HTTP/2 pseudo-header pairs (:authority, :method, ...).

    In two-line DevTools format the value line would otherwise be parsed
    as a header name, and colon-format keys would crash requests.
    """
    lines = raw.splitlines()
    kept: list[str] = []
    skip_value = False
    for line in lines:
        if skip_value:
            skip_value = False
            continue
        stripped = line.strip()
        if stripped.startswith(":"):
            if ":" not in stripped[1:]:
                skip_value = True
            continue
        kept.append(line)
    return "\n".join(kept)


def _clean_saved_headers(data: dict) -> dict:
    """Drop leftovers the library's lowercase-only filter misses.

    ``accept-encoding`` is dropped (not normalized): requests then applies
    its own ``gzip, deflate`` default, which urllib3 can actually decode.
    """
    cleaned = {}
    for key, value in data.items():
        low = key.lower()
        if low.startswith(":") or low.startswith("sec") or low in _DROP_EXACT:
            continue
        cleaned[key] = value
    return cleaned


def save_browser_headers(raw: str) -> None:
    """Parse pasted devtools headers or cURL (bash) into browser_headers.json.

    Writes to a temp file, structurally validates it by constructing a client
    (no network), and only then replaces any previously stored headers.
    """
    from ytmusicapi.auth.browser import setup_browser

    target = browser_path()
    tmp = target.with_name(target.name + ".tmp")
    try:
        raw = _strip_pseudo_headers(_curl_to_header_lines(raw))
        try:
            setup_browser(filepath=str(tmp), headers_raw=raw)
        except YTMusicError as exc:
            raise HeadersError(str(exc)) from exc
        data = json.loads(tmp.read_text(encoding="utf-8"))
        # type detection needs a SAPISIDHASH marker; the real header is
        # regenerated from the cookie on every request
        if "SAPISIDHASH" not in str(data.get("authorization", "")):
            data["authorization"] = "SAPISIDHASH0:0"
        data = _clean_saved_headers(data)
        _write_private(tmp, data)
        try:
            YTMusic(auth=str(tmp))
        except YTMusicError as exc:
            raise HeadersError(str(exc)) from exc
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def verify_browser_session() -> dict:
    """Live account check for the saved headers; deletes them when rejected."""
    path = browser_path()
    try:
        info = YTMusic(auth=str(path)).get_account_info()
    except Exception as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HeadersError(f"Browser session rejected: {exc}") from exc
    return info if isinstance(info, dict) else {}


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
    interval = code.interval

    while True:
        if should_cancel is not None and should_cancel():
            raise CancelledLogin("cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PollError("Login code expired before approval")
        try:
            raw = creds.token_from_code(code.device_code)
        except requests.RequestException:
            sleep(min(interval, remaining))
            continue
        except YTMusicError as exc:
            raise PollError(str(exc)) from exc
        if isinstance(raw, dict) and "access_token" in raw:
            if "refresh_token" not in raw:
                raise PollError("Google returned no refresh token — retry the login")
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
            sleep(min(interval, remaining))
            continue
        if error == "slow_down":
            interval += 5
            sleep(min(interval, remaining))
            continue
        if error in ("expired_token", "access_denied"):
            raise PollError(f"Login {error.replace('_', ' ')}")
        raise PollError(f"Login failed: {error or raw}")
