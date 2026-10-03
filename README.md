# yt-tui-player

Stream YouTube Music from your terminal. Search, queue, and control playback
without ever leaving the keyboard — a Textual TUI backed by `mpv`.

## Features

- **Live search** — debounced YouTube Music song search as you type
- **Vim-style navigation** — `hjkl`, `gg`/`G`, `ctrl+d/u/f/b`, vim `?` help overlay,
  and a `-- NORMAL --` / `-- INSERT --` mode chip (focus *is* the mode: typing in
  search = INSERT, tables = NORMAL)
- **Vim statusline** — mode, now-playing, progress, volume, queue position, plus a
  context-sensitive hint line that changes per tab/mode
- **Instant playback** — `Enter` on a result streams it through `mpv`
- **Queue management** — add, remove, jump-to; auto-advances when a track ends;
  the playing track is marked with a green `▶` in both the results and the queue
- **Multi-select → playlists** — `s` toggles ● marks on songs, `S` clears them,
  and `A` adds all marked songs to one of your saved YouTube Music playlists
  (falls back to the cursor row when nothing is marked)
- **Five tabs, keys `1`–`5`** — Results, Queue, Library, History, Profile
  (`r`/`q` still jump to Results/Queue)
- **Library browser** — your saved playlists, albums and artists in one pane
  with section tabs (`tab` / `←`/`→` inside it); `Enter` on a playlist opens
  its songs — play, queue and mark them like search results, `esc` goes back.
  Albums and artists are display-only for now.
- **Play history** — the last 200 plays grouped under shelf labels (Today,
  Yesterday, This week, …); `Enter` replays any of them (read-only in v1)
- **Listening profile** — account card with half-block avatar, plus minutes
  and plays per artist merged from YouTube Music's history *and* a local play
  tracker (`local_stats.json`, mode 600); guests see local-only stats
- **Two sign-in methods** — optional, unlocks your library, history and
  profile (plus the `A` playlist picker); search and playback work logged-out
  either way:
  * **Browser session** (`ctrl+l` → `ctrl+b`) — paste a *Copy as cURL (bash)*
    command (or raw request headers) copied from a logged-in music.youtube.com
    tab. No Google Cloud setup at all; re-paste whenever the session expires.
  * **Google device-code OAuth** (`ctrl+l`) — paste your own OAuth client
    credentials once, approve a code on any device; the refreshing token is
    stored locally (mode 600).
- **Album art** — the now-playing cover renders as truecolor half-blocks in a
  right-hand column with title/artist/duration beneath; auto-hides below 110
  columns so the tables keep their width
- **Themes** — cycle 21 built-in Textual themes with `t` (`YT_TUI_THEME=gruvbox`
  to pick one at startup)
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
  ├── widgets.py  (statusline PlayerBar, mode chip, hints, help overlay)
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

**Vim modes.** Focus defines the mode: with the search input focused you are in
`-- INSERT --` (typing searches live), with a table focused you are in
`-- NORMAL --` where all the navigation keys work. `esc` always returns to
NORMAL; `/` enters INSERT. The mode chip and hint line re-render off
`Screen.focused` (a reactive), so they never go stale.

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

### NORMAL mode (table focused)

| Key | Action |
|---|---|
| `j` / `k` or `↑`/`↓` | Move cursor |
| `gg` / `G` | First / last row |
| `ctrl+d` / `ctrl+f` | Half / full page down |
| `ctrl+u` / `ctrl+b` | Half / full page up |
| `h` / `l` or `←`/`→` | Seek ∓5s |
| `space` | Play / pause |
| `n` / `p` | Next / previous (`p` restarts if >3s in) |
| `+` / `-` | Volume up / down |
| `s` | Toggle ● mark on the song under the cursor |
| `S` | Clear all marks |
| `a` | Queue marked songs (cursor row when nothing is marked) |
| `A` | Add marked songs to a playlist (picker) |
| `d` | Remove selected queue entry |
| `Enter` (result) | Play now (or jump to it if already queued) |
| `Enter` (queue) | Jump to / play that queue entry |
| `Enter` (library) | Open a playlist's songs (`esc` leaves the view) |
| `Enter` (history) | Play that past track |
| `/` | Focus search → INSERT mode |
| `r` / `q` | Results / Queue tab (same as `1` / `2`) |
| `1`–`5` | Results / Queue / Library / History / Profile tab |
| `Tab` | Cycle focus |
| `t` | Cycle theme |
| `?` | Toggle help overlay (`esc`/`q`/`?` closes; `j`/`k` scroll) |
| `ctrl+l` | Log in (browser session or device-code OAuth) |
| `ctrl+b` | Inside login: switch to browser-session sign-in |
| `ctrl+q` | Quit |

### INSERT mode (search focused)

| Key | Action |
|---|---|
| type | Live search (debounced) |
| `Enter` | Search now and jump to results |
| `esc` | Back to NORMAL mode |

## Sign in (optional)

Login unlocks the Library, History and Profile tabs (`1`–`5`) plus the `A`
playlist picker; search and playback work without it.
Two methods — pick either:

### Browser session (no Google Cloud needed)

1. In a browser, open [music.youtube.com](https://music.youtube.com) and sign in.
2. `F12` → **Network** tab → click any `youtubei/v1/…` request → right-click it →
   **Copy → Copy as cURL (bash)** (Firefox: **Copy → Copy Request Headers**;
   manual fallback: select the whole *Request Headers* block — it must include
   `cookie` and `x-goog-authuser`).
3. In the app press `ctrl+l` → **`ctrl+b`** → paste → **Enter**. Both the
   cURL command and raw header lines are accepted.
4. The app verifies the session against your account and shows
   *"Logged in as …"* — stored as `browser_headers.json` (mode 600).

Re-paste with the same steps whenever the session expires (password change,
browser logout, etc.); the app tells you when it is rejected.

### Google device-code OAuth

1. One-time: create OAuth client credentials in the
   [Google Cloud Console](https://console.cloud.google.com) —
   *APIs & Services → Credentials → Create credentials → OAuth client ID →
   Application type: **TVs and Limited Input devices*** — and either publish
   the OAuth consent screen or add your account under *Test users*.
2. In the app press `ctrl+l` and paste the client ID and secret (stored in
   `~/.config/yt-tui-player/`, mode 600 — never committed anywhere).
3. Open the shown URL on any device, enter the code, and approve.
   The token auto-refreshes afterwards; delete `oauth.json` to log out.

## Development

```bash
.venv/bin/ruff check .   # lint
.venv/bin/pytest         # unit tests
```

Test coverage: the queue model, track/duration parsing, statusline
hint/progress helpers, the OAuth config/device-poll loop (mocked),
browser-header parsing/storage and client-priority selection, multi-select
mark logic, album-art URL/thumbnail/half-block rendering, the library /
history / account adapters, profile aggregation and local-stats storage,
tab-key dispatch and guest placeholders — **109 tests**.
The full flow (search → mark `s` → playlist picker → guest login screen →
login method toggle → queue ops → playback with real album art → resize
auto-hide → help overlay → `ctrl+l` → tab keys `1`–`5` with guest
library/history/profile panes → theme/seek/volume → local play-stats file)
is verified end-to-end with Textual's headless pilot harness driving a real
mpv against the `null` audio output (`YT_TUI_MPV_EXTRA="--ao=null"`).

## Troubleshooting

- **"mpv failed to start"** — install mpv (`pacman -S mpv` / `apt install mpv`).
- **"Failed to play" toasts** — YouTube occasionally rejects freshly resolved
  stream URLs (HTTP 403). The app retries once automatically; updating yt-dlp
  (`pip install -U yt-dlp`) fixes persistent cases.
- **No sound** — verify mpv standalone: `mpv --no-video <url>`.
- **Login rejected (`invalid_client` / `unauthorized_client`)** — client ID and
  secret don't match, or the OAuth client type isn't *TVs and Limited Input
  devices*. Press `ctrl+l` and paste corrected credentials.
- **`403 access_denied` on the approval page ("app has not completed Google
  verification")** — the OAuth consent screen is in *Testing* and your account
  isn't a test user. Either *Publish app* / add a test user under
  *APIs & Services → OAuth consent screen*, **or skip OAuth entirely**: sign in
  with browser-session headers (`ctrl+l` → `ctrl+b`).
- **"Browser session rejected"** — the pasted cookies expired or belong to a
  different site. Re-copy the headers from music.youtube.com and paste again
  (`ctrl+l` → `ctrl+b`).
- **Login code expired** — approve faster, or press `ctrl+l` to fetch a new code.
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
- [ ] Play-next insertion (key TBD — `A` now means playlist-add)
- [ ] Config file (`~/.config/yt-tui-player/config.toml`): default volume,
      ytdl format, debounce, keymap
- [ ] Search history / recall (`Ctrl+R`)
- [ ] Result caching to avoid repeat network calls
- [ ] Sixel/kitty image protocols for sharper art where the terminal supports
      it (half-block fallback already ships)
- [ ] MPRIS integration (desktop media keys / widget)
- [ ] Package for PyPI + `pipx install yt-tui-player`

### Known limitations
- Queue and marks reset on exit (oauth token persists)
- History is read-only (no removal), and library albums/artists are
  display-only — only playlists drill into playable songs
- Library / History / Profile need sign-in; the profile's "recent history"
  column uses only YouTube's last 200 plays, "This app" only counts listens
  made while this player was running
- Narrow terminals (< ~110 cols) hide the cover column and scroll the tables
  horizontally
- YouTube stream URL 403s are retried but not solved (cookies/PO-token support
  could fix permanently — tie-in with the login task)
