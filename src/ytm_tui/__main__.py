"""Entry point: `python -m ytm_tui` or the `yt-tui` script."""

from __future__ import annotations

import signal

from .app import YTMusicTUI


def _sigterm_handler(signum: int, frame: object) -> None:
    raise SystemExit(143)


def main() -> None:
    # Ensure mpv gets cleaned up when the process is terminated (SIGTERM).
    signal.signal(signal.SIGTERM, _sigterm_handler)
    app = YTMusicTUI()
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()


if __name__ == "__main__":
    main()
