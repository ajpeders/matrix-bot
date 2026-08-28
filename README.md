# matrix-bot

A Matrix music bot that streams audio into Element Call (LiveKit) voice rooms. Drop a YouTube URL or upload an audio file in Matrix and it plays in the call.

## Features

- Plays YouTube tracks (single videos) and YouTube search results
- Plays uploaded audio attachments (mp3, ogg, flac, wav, m4a, opus, aac, wma) — including end-to-end encrypted attachments
- Queue with skip / stop / pause / resume
- Per-track rewind and fast-forward
- Per-track volume (0–200 %)
- Joins Element Call via `org.matrix.msc3401.call.member` so the bot shows up as a real call participant
- E2EE-aware: shares megolm group sessions before sending and decrypts incoming encrypted audio uploads

## Commands

### Playback

| Command | Description |
|---|---|
| `!play <url\|query>` | Queue a YouTube/Spotify URL or treat the rest as a search and queue the top hit |
| `!play <n>` | Queue result #n from your last `!search` |
| `!play` | Bot waits ~5 min for you to upload an audio file, then queues it |
| `!search <query>` | Show top 5 YouTube hits to pick from |
| `!skip` | Skip the current track |
| `!stop` | Stop playback and clear the queue |
| `!pause` / `!resume` | Pause or resume |
| `!rewind [secs]` / `!ff [secs]` | Jump back / forward (default 15s) |
| `!shuffle` | Shuffle the current queue |
| `!queue` | Show what's queued |
| `!np` | Show now-playing |
| `!volume <0-200>` | Set volume (100 = normal) |
| `!help` | Show command list |

### One-shot playlists from URLs

Auto-detected on `!play <url>` for YouTube, Spotify playlists/albums. Capped at 50 tracks for safety.

| Command | Description |
|---|---|
| `!playlist <url> [N]` | Queue the first N tracks of a playlist (default 50). Works for any yt-dlp-supported playlist URL — YouTube, SoundCloud sets, Bandcamp albums, Spotify playlists/albums, Mixcloud, etc. |

For Spotify (track / album / playlist URLs), the bot fetches the title + artist via the Spotify Web API and searches YouTube for each track. Requires `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` to be set.

### Saved playlists

Persisted as JSON in `STATE_DIR/playlists.json`, survive restarts. Shared across users.

| Command | Description |
|---|---|
| `!pl create <name>` | Create a new saved playlist |
| `!pl add <name> <url\|query>` | Add a track (URL or search) to a saved playlist |
| `!pl play <name>` | Queue the whole playlist |
| `!pl shuffle <name>` | Queue the playlist in random order |
| `!pl list` | List all saved playlists |
| `!pl show <name>` | Show all tracks in a saved playlist |
| `!pl rm <name> <n>` | Remove track #n from a saved playlist |
| `!pl delete <name>` | Delete a saved playlist |

## Configuration

Environment variables (typically supplied via `.env` next to the compose file):

| Variable | Description |
|---|---|
| `MATRIX_HOMESERVER` | e.g. `https://chat.example.com` |
| `MATRIX_USER` | Bot's Matrix user ID, e.g. `@jukebox:chat.example.com` |
| `MATRIX_PASSWORD` | Bot's password (used once, then a saved access token is reused) |
| `LIVEKIT_URL` | LiveKit websocket URL, e.g. `wss://livekit.example.com` |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit credentials |
| `LIVEKIT_SERVICE_URL` | Element Call's lk-jwt service URL, e.g. `https://lk-jwt.example.com` |
| `VOICE_ROOM_ID` | Matrix room ID where the call lives, e.g. `!abc:chat.example.com` |
| `SPOTIFY_CLIENT_ID` | (Optional) Spotify dev app client ID — required for Spotify URL support |
| `SPOTIFY_CLIENT_SECRET` | (Optional) Spotify dev app client secret |
| `STATE_DIR` | Persistent state directory (default `/app/state`) — holds the crypto store, saved login, saved playlists |
| `WEB_ENABLE` | Set to `0` to disable the web API entirely (default on) |
| `WEB_PORT` | Port the in-process web API listens on (default `8080`) |
| `WEB_PASSWORD` | (Optional) Shared password gating the web UI. Empty = open (LAN-only) |
| `WEB_API_KEY` | (Optional) Strong random value: token-signing secret + `X-API-Key` for non-browser clients |

## Web UI

The bot serves an in-process HTTP API (aiohttp, on `WEB_PORT`) that a separate
React SPA (`frontend/`) talks to — drop URLs, search, manage the queue, and
edit saved playlists from a browser. Mirrors the [discordbot](../discordbot)
web layer, flattened to this bot's single voice-room model.

- `config.py` / `web/` — the API: status, now-playing, search, queue
  add/remove/move/seek, transport, volume, one-shot playlist URL import, and
  saved-playlist CRUD + play. Auth is a stateless HMAC bearer token from
  `WEB_PASSWORD` (plus optional `X-API-Key`).
- `frontend/` — Vite + React + Tailwind SPA, built and served by nginx on port
  5173. Traefik routes `/api/*` to the bot and everything else to the SPA.

```sh
cd frontend
npm install
npm run dev      # dev server, proxies /api to http://localhost:8080
npm run build    # production bundle into dist/
npm test         # vitest
```

## Running

This repo ships its own `docker-compose.yml` defining both the bot
(`matrix-musicbot`) and the web UI (`matrix-musicbot-web`).

**Standalone:**

```sh
cp .env.example .env   # then fill in Matrix/LiveKit creds
docker compose up -d --build
```

**In the homelab stack:** `services/matrix-musicbot/docker-compose.yml`
`include:`s this file and feeds it `services/matrix-musicbot/.env`, so it comes
up with the rest of the stack (`cd services && docker compose up -d`). To
rebuild just this service:

```sh
docker compose -f services/matrix-musicbot/docker-compose.yml up -d --build
```

## Repo layout

```
bot.py             # all bot logic (Matrix client, LiveKit publish, ffmpeg pipeline)
config.py          # web-API settings read from the environment
web/               # in-process aiohttp API (server.py, auth.py)
frontend/          # Vite + React + Tailwind SPA (built + served by nginx)
Dockerfile         # python:3.12-slim + ffmpeg + libolm3
requirements.txt   # matrix-nio[e2e], livekit, livekit-api, yt-dlp, aiohttp
tests/             # pytest suite
```

See `ARCHITECTURE.md` for a deeper walkthrough of how playback and call signaling work.
