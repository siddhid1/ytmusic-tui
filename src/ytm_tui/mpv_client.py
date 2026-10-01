"""mpv subprocess management + JSON IPC over a unix socket.

One mpv process lives for the lifetime of the app (`--idle=yes`); each track is
played with `loadfile <url>`. A single reader thread owns the socket: outgoing
commands are matched to incoming responses via `request_id` futures, and
unsolicited events (`end-file`, `property-change`, ...) are forwarded to a
handler callable (invoked from the reader thread).
"""

from __future__ import annotations

import ctypes
import itertools
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

DEFAULT_YTDL_FORMAT = "bestaudio/best"

# Kill mpv if our process dies without a clean shutdown (Linux only).
_PR_SET_PDEATHSIG = 1
_libc = None
if sys.platform == "linux":
    try:
        _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        _libc = None


def _pdeathsig() -> None:
    if _libc is not None:
        _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)


class MpvError(RuntimeError):
    """Raised when mpv cannot be started or a command fails."""


class MpvClient:
    def __init__(
        self,
        *,
        binary: str = "mpv",
        socket_path: Path | None = None,
        ytdl_format: str = DEFAULT_YTDL_FORMAT,
        volume: int = 100,
        extra_args: list[str] | None = None,
    ) -> None:
        self._binary = binary
        self._ytdl_format = ytdl_format
        self._volume = volume
        self._extra_args = list(extra_args or [])
        runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        self._socket_path = socket_path or Path(runtime) / f"yt-tui-{os.getpid()}.sock"
        self._proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._pending: dict[int, Future[Any]] = {}
        self._pending_lock = threading.Lock()
        self._request_ids = itertools.count(1)
        self._event_handler: Callable[[dict[str, Any]], None] | None = None
        self._dead = threading.Event()
        self._last_time_pos_push = 0.0
        self._stderr_path = Path(tempfile.gettempdir()) / f"yt-tui-mpv-{os.getpid()}.log"
        self._log_file = None

    # -- lifecycle ----------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and not self._dead.is_set()

    def set_event_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        self._event_handler = handler

    def start(self, timeout: float = 15.0) -> None:
        """Spawn mpv, connect IPC, start the reader thread. Raises MpvError."""
        if self.is_running:
            return
        try:
            self._socket_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._log_file = open(self._stderr_path, "wb")  # noqa: SIM115
        try:
            self._proc = subprocess.Popen(
                [
                    self._binary,
                    "--no-video",
                    "--no-terminal",
                    "--idle=yes",
                    "--force-window=no",
                    f"--input-ipc-server={self._socket_path}",
                    f"--ytdl-format={self._ytdl_format}",
                    "--audio-client-name=yt-tui-player",
                    f"--volume={self._volume}",
                    f"--log-file={self._stderr_path}",
                    *self._extra_args,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self._log_file,
                preexec_fn=_pdeathsig if os.name == "posix" else None,
            )
        except FileNotFoundError as exc:
            self._log_file.close()
            raise MpvError(f"mpv binary not found: {self._binary!r}") from exc
        except OSError as exc:
            self._log_file.close()
            raise MpvError(f"failed to spawn mpv: {exc}") from exc

        deadline = time.monotonic() + timeout
        while not self._socket_path.exists():
            if self._proc.poll() is not None:
                raise MpvError(f"mpv exited early: {self._read_log_tail()}")
            if time.monotonic() > deadline:
                self.close()
                raise MpvError("timed out waiting for mpv IPC socket")
            time.sleep(0.05)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        while True:
            try:
                self._sock.connect(str(self._socket_path))
                break
            except OSError:
                if time.monotonic() > deadline:
                    self.close()
                    raise MpvError("timed out connecting to mpv IPC socket") from None
                if self._proc.poll() is not None:
                    raise MpvError(
                        f"mpv exited early: {self._read_log_tail()}"
                    ) from None
                time.sleep(0.05)

        self._dead.clear()
        self._reader = threading.Thread(target=self._reader_loop, daemon=True, name="mpv-ipc")
        self._reader.start()

        # Ping + start property observation (events drive the UI, no polling needed).
        self.command("get_property", "pause")
        for prop_id, name in ((1, "time-pos"), (2, "pause"), (3, "duration"), (4, "volume")):
            self.command("observe_property", prop_id, name)

    def close(self) -> None:
        """Terminate mpv and clean up socket. Never raises."""
        if self.is_running:
            try:
                self.command("quit", timeout=1.0)
            except (MpvError, OSError):
                pass
        self._dead.set()
        if self._proc is not None:
            try:
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait(timeout=2.0)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=2.0)
        self._fail_pending(MpvError("mpv connection closed"))
        try:
            self._socket_path.unlink(missing_ok=True)
        except OSError:
            pass
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    # -- commands -----------------------------------------------------------
    def command(self, *args: Any, timeout: float = 5.0) -> Any:
        """Send an IPC command; returns response `data`, raises MpvError on failure."""
        if self._sock is None or self._dead.is_set():
            raise MpvError("mpv is not running")
        request_id = next(self._request_ids)
        future: Future[Any] = Future()
        with self._pending_lock:
            self._pending[request_id] = future
        payload = json.dumps({"command": list(args), "request_id": request_id}) + "\n"
        try:
            with self._send_lock:
                self._sock.sendall(payload.encode())
        except OSError as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise MpvError(f"failed to send command to mpv: {exc}") from exc
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise MpvError(f"mpv command timed out: {args[0]}") from exc

    def load(self, url: str) -> None:
        self.command("loadfile", url, "replace")

    def play_pause(self) -> None:
        self.command("cycle", "pause")

    def seek_relative(self, seconds: float) -> None:
        self.command("seek", seconds, "relative", "exact")

    def set_volume(self, value: int) -> None:
        self.command("set_property", "volume", max(0, min(150, value)))

    # -- reader thread ------------------------------------------------------
    def _reader_loop(self) -> None:
        assert self._sock is not None
        buffer = b""
        try:
            while not self._dead.is_set():
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line.strip():
                        self._dispatch(line)
        except OSError:
            pass
        finally:
            self._dead.set()
            self._fail_pending(MpvError("mpv connection lost"))
            handler = self._event_handler
            if handler is not None:
                try:
                    handler({"event": "mpv-connection-lost"})
                except Exception:
                    pass

    def _dispatch(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict):
            return
        if message.get("event") == "property-change" and message.get("name") == "time-pos":
            # Throttle progress updates: mpv reports time-pos very frequently.
            if message.get("data") is not None:
                now = time.monotonic()
                if now - self._last_time_pos_push < 0.25:
                    return
                self._last_time_pos_push = now
        if "event" in message:
            handler = self._event_handler
            if handler is not None:
                try:
                    handler(message)
                except Exception:
                    pass  # never kill the reader because of a UI handler bug
            return
        request_id = message.get("request_id")
        if request_id is None:
            return
        with self._pending_lock:
            future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return
        error = message.get("error", "success")
        if error not in ("success", None):
            future.set_exception(MpvError(f"mpv command failed: {error}"))
        else:
            future.set_result(message.get("data"))

    def _fail_pending(self, error: Exception) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    def _read_log_tail(self, lines: int = 10) -> str:
        try:
            content = self._stderr_path.read_text(errors="replace").strip().splitlines()
            return " | ".join(content[-lines:]) or "(no output)"
        except OSError:
            return "(log unavailable)"
