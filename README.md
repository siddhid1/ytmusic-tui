# yt-tui-player

Stream YouTube Music from your terminal. Search, queue, and control playback
without ever leaving the keyboard — a Textual TUI backed by `mpv`.

## Features

- **Live search** — debounced YouTube Music song search as you type
- **Instant playback** — `Enter` on a result streams it through `mpv`
- **Queue management** — add, remove, jump-to; auto-advances when a track ends
- **Player bar** — now playing, progress bar, elapsed/total time, volume, queue position
- **Transport controls** — pause/resume, next/previous, seek ±5s, volume up/down
- **Resilient playback** — transient stream failures (e.g. YouTube HTTP 403) are
  retried once, then skipped with a toast notification
- **Zero config** — no login required for search/stream; runs out of the box

## How it works

```
Textual app (asyncio event loop)
  ├── search input ──debounce──► worker thread ──► ytmusicapi ──► YouTube Music API
  │                                   │ (results marshalled back via call_from_thread)
  ├── QueueModel  (pure Python: items + cursor, unit-tested, no I/O)
  └── MpvClient   (unix-socket JSON IPC, dedicated reader thread)
        └── mpv --idle=yes ──► yt-dlp resolves music.youtube.com/watch?v=…
                                └──► ffmpeg streams audio to your sound server
```

**Playback flow**

1. `Enter` on a search result → `_play_now()` enqueues the track (or jumps to it
   if already queued) and issues `loadfile <url>` over mpv's IPC socket.
2. mpv invokes its built-in yt-dlp hook, resolves the best audio format
   (`bestaudio/best`), and starts streaming.
3. A single reader thread owns the socket: responses are matched to commands via
   `request_id` futures; unsolicited events (`property-change`, `end-file`,
   `file-loaded`) are marshalled onto the UI thread with `call_from_thread`.
4. Observed properties (`time-pos`, `pause`, `duration`, `volume`) drive the
   player bar; `time-pos` is throttled to 4 Hz to keep redraws cheap.
5. On `end-file` → the queue cursor advances and the next track loads.
   `reason=error` retries the same track once, then skips with a toast.

**Why our own queue instead of mpv's playlist?** Reordering, inserting
mid-queue, removing the currently playing track, and jumping around all need
cursor semantics mpv doesn't expose cleanly — `QueueModel` keeps that logic
pure and fully unit-tested while mpv plays exactly one file at a time.

## Requirements

- Python ≥ 3.11
- [`mpv`](https://mpv.io) on `PATH`
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) on `PATH`
  (`pip install yt-dlp` works too)

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Run

```bash
.venv/bin/python -m ytm_tui
# or
.venv/bin/yt-tui
```

## Keybindings

| Key | Action |
|---|---|
| `/` | Focus search input |
| `Enter` (search) | Search now, jump to results |
| `Enter` (result) | Play now (or jump to it if already queued) |
| `Enter` (queue) | Jump to / play that queue entry |
| `↑`/`↓` or `j`/`k` | Move cursor |
| `Tab` | Switch focus (search ↔ table) |
| `q` | Open Queue tab |
| `space` | Play / pause |
| `n` / `p` | Next / previous (`p` restarts if >3s in) |
| `←` / `→` | Seek ∓5s |
| `+` / `-` | Volume up / down |
| `a` | Add selected result to queue |
| `d` | Remove selected queue entry |
| `esc` | Back from search to results |
| `?` | Show keybinding help |
| `ctrl+q` | Quit |

## Development

```bash
.venv/bin/ruff check .   # lint
.venv/bin/pytest         # unit tests (queue model, parsing)
```

Test coverage: the queue model and track/duration parsing are unit-tested.
The full flow (search → play → queue ops → seek → pause → next) is verified
end-to-end with Textual's headless pilot harness driving a real mpv against the
`null` audio output (`YT_TUI_MPV_EXTRA="--ao=null"`).

## Troubleshooting

- **"mpv failed to start"** — install mpv (`pacman -S mpv` / `apt install mpv`).
- **"Failed to play" toasts** — YouTube occasionally rejects freshly resolved
  stream URLs (HTTP 403). The app retries once automatically; updating yt-dlp
  (`pip install -U yt-dlp`) fixes persistent cases.
- **No sound** — verify mpv standalone: `mpv --no-video <url>`.
- **mpv debug log** — written to `/tmp/yt-tui-mpv-<pid>.log`.

## TODOs

### Next (v2)
- [ ] **Lyrics** — synced LRC display via LRCLIB (`api/get` + `api/search`,
      plain-text fallback, instrumental handling) — API already validated
- [ ] **Auto-radio** — when the queue runs out, append related tracks from
      YT Music's watch playlist (`get_watch_playlist`) — API already validated
- [ ] **Browse** — drill into albums, artists, and playlists from search results
      (`get_album` / `get_artist` / `get_playlist` all verified working)

### Then
- [ ] Shuffle + repeat modes (off / all / one)
- [ ] Reorder queue with `K`/`J` (model logic already implemented + tested)
- [ ] Play-next (`A`) insertion
- [ ] Config file (`~/.config/yt-tui-player/config.toml`): default volume,
      ytdl format, debounce, keymap
- [ ] Search history / recall (`Ctrl+R`)
- [ ] Google login via ytmusicapi headers → liked songs, saved playlists,
      your library (deferred: unauthenticated mode is the v1 baseline)
- [ ] Result caching to avoid repeat network calls
- [ ] Track thumbnails (sixel/kitty graphics) where the terminal supports it
- [ ] MPRIS integration (desktop media keys / widget)
- [ ] Package for PyPI + `pipx install yt-tui-player`

### Known limitations
- No persistent state — queue and settings reset on exit
- Narrow terminals (< ~110 cols) scroll the tables horizontally
- YouTube stream URL 403s are retried but not solved (cookies/PO-token support
  could fix permanently — tie-in with the login task)
