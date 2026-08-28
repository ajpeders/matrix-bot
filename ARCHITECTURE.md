# Architecture

The bot is one process built around two clients that have to stay coordinated: a Matrix client (for commands, state events, and E2EE attachments) and a LiveKit room (for actual audio publication).

## Process layout

```
                 ┌──────────────────┐
                 │   matrix-nio     │  commands, state events,
                 │   AsyncClient    │  E2EE key exchange, attachments
                 └────────┬─────────┘
                          │
       ┌──────────────────┴──────────────────┐
       │             MusicBot                │
       │   - per-room RoomState              │
       │   - playback_loop (asyncio.Task)    │
       └──────────────────┬──────────────────┘
                          │
                 ┌────────┴─────────┐
                 │   livekit.rtc    │  WebRTC publish to the
                 │   Room + Track   │  LiveKit SFU
                 └──────────────────┘
```

The bot also spawns short-lived subprocesses:
- `yt-dlp` to fetch / search YouTube
- `ffmpeg` to demux any source into 48 kHz mono s16le PCM

## Per-room state

Most playback state is keyed by Matrix room ID in `MusicBot.rooms`. The single most important rule: the **voice room** (where the LiveKit call lives, set by `VOICE_ROOM_ID`) holds all playback state, while commands can come from any room (the "music room"). When a command is received, the bot looks up `voice_room_id`'s `RoomState` for queue / playback control, but sends user-facing messages back to whichever room the command came from.

`RoomState` carries:
- `queue` (deque of `Track`)
- `current` (now playing)
- `lk_room`, `audio_source` (the LiveKit publication)
- `playback_task` (the asyncio task running `playback_loop`)
- `skip_event` / `stop_event` (one-shot signals into the streaming loop)
- `start_lock` (serializes `ensure_playback` so concurrent `!play`s don't double-spawn)
- `call_member_task` (refreshes the Element Call membership state event)
- `play_started_at` / `play_seek_offset` (used by `!rewind` and `!ff`)

## Playback pipeline

1. `!play <url>` → `_yt_resolve` runs `yt-dlp --dump-json --no-playlist` to grab the title and produce a `Track` with `source = "yt://<url>"`.
2. `ensure_playback` makes sure the LiveKit room is connected and there's a running `playback_loop` task.
3. `playback_loop` pops tracks and calls `stream_audio`.
4. `stream_audio` for YouTube sources sets up an `os.pipe()` and runs `yt-dlp -o -` writing into the pipe; `ffmpeg` reads the read-end as `pipe:0`. For local files it just feeds the path to ffmpeg.
5. ffmpeg emits raw 48 kHz mono s16le PCM. The bot reads 10 ms frames (480 samples = 960 bytes), optionally rescales for volume, and feeds them to `livekit.rtc.AudioSource.capture_frame`.
6. ffmpeg's `-re` flag paces the read at real-time, which is what keeps streaming sane.

### Why pipe yt-dlp into ffmpeg instead of writing a temp file

Earlier the bot wrote yt-dlp output to a tempfile then handed the path to ffmpeg. This kept failing with `EBML header parsing failed` — yt-dlp's chosen format (m4a, opus, webm, etc.) is decided at runtime, and any extension we picked for the tempfile would mislead ffmpeg's demuxer. Piping eliminates the format-guess game entirely.

The pipe is an OS-level `os.pipe()` rather than `yt_proc.stdout` because asyncio's `StreamReader` lacks a `fileno()` and can't be passed as another subprocess's stdin.

## Element Call integration

Element Call doesn't subscribe to LiveKit participants on the SFU's word alone — clients only render participants whose Matrix call membership is published as an `org.matrix.msc3401.call.member` state event in the same Matrix room.

So `ensure_livekit` does three things in order:
1. Pick the LiveKit room name (currently the most-populated active room — see "Known limitations").
2. Connect to LiveKit with an identity formatted like `@user:domain:devicesuffix`. Bare identities (e.g. `music-bot`) get filtered out by Element Call.
3. Publish a `call.member` state event in the Matrix voice room declaring the bot's `foci_preferred` (LiveKit alias + service URL), and start a refresh task that republishes every hour. On playback end, the state event is tombstoned (set to `{}`).

## Encryption

The Matrix client is configured with `encryption_enabled=True` and a persistent crypto store at `STATE_DIR/store`. Two paths matter:

**Outgoing**: `send()` checks if the destination room is encrypted and calls `share_group_session` if a fresh megolm session is needed. Without this, matrix-nio falls back to plaintext silently — that was the cause of the early "(not encrypted)" reports.

**Incoming attachments**: For E2EE rooms, `RoomMessageAudio.url` is `None` and the actual ciphertext + per-file key live under `event.source["content"]["file"]` (`{url, key, iv, hashes}`). `_download_attachment` checks for that envelope and runs the bytes through `nio.crypto.decrypt_attachment`.

## Seek / rewind

ffmpeg's `-ss` flag does demuxer-level seek when the input is seekable. For local files that's a fast random-access seek; for our yt-dlp pipe it falls back to decode-and-discard, which is fine for the typical 15-second jump.

`_seek_relative` calculates the current position from `play_started_at` + `play_seek_offset`, builds a fresh `Track` with `seek=new_position` and the same source/cleanup_path, prepends it to the queue, and sets `skip_event` so the current ffmpeg loop tears down. The next iteration of `playback_loop` picks up the prepended track.

`playback_loop` doesn't unlink the cleanup_path if another queued track still references the same file — important so a `!rewind` on a local upload doesn't delete the file it's about to replay.

## Persistent state

Lives under `STATE_DIR` (mounted from `state/matrix-musicbot/` in the homelab):
- `credentials.json` — saved access token / device_id, restored on startup so we keep the same megolm sessions across restarts
- `store/` — matrix-nio's encryption store (olm sessions, megolm sessions, device keys)

## Known limitations

- `_find_active_livekit_room` picks whichever LiveKit room has the most participants. With multiple concurrent calls on the same homeserver, the bot could join the wrong one. The right fix is to derive the LiveKit alias from `VOICE_ROOM_ID` directly (Element Call uses the Matrix room ID as the alias).
- Queue lives in process memory only — a restart loses it.
- Volume is applied per-frame in pure Python with a saturating multiply. Fine at 48 kHz mono, but if it ever moves to stereo and someone sets non-default volume, this is the obvious hot spot.
- `!pause` blocks the read loop with a 50 ms sleep; the `play_started_at`/`play_seek_offset` math doesn't subtract paused time, so a `!rewind` after a long pause will compute a position that's ahead of where playback actually is. In practice the discrepancy is small.
