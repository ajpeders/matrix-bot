#!/usr/bin/env python3
"""Matrix music bot — streams audio into LiveKit voice rooms."""

import asyncio
import array
import json
import logging
import os
import random
import re
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiohttp
from livekit import api, rtc
from nio import (
    AsyncClient,
    AsyncClientConfig,
    LoginResponse,
    MatrixRoom,
    MegolmEvent,
    RoomMessageAudio,
    RoomMessageFile,
    RoomMessageText,
    SyncResponse,
)
from nio.crypto import decrypt_attachment

import config

# Persistent state for E2EE: crypto store + login credentials so we keep the same device_id
STATE_DIR = Path(os.environ.get("STATE_DIR", "/app/state"))
CRYPTO_STORE_DIR = STATE_DIR / "store"
CREDENTIALS_PATH = STATE_DIR / "credentials.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MATRIX_HOMESERVER = os.environ["MATRIX_HOMESERVER"]
MATRIX_USER = os.environ["MATRIX_USER"]
MATRIX_PASSWORD = os.environ["MATRIX_PASSWORD"]
LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
LIVEKIT_SERVICE_URL = os.environ.get("LIVEKIT_SERVICE_URL", "")  # e.g. https://lk-jwt.example.com
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

CALL_MEMBER_TYPE = "org.matrix.msc3401.call.member"
CALL_MEMBER_EXPIRES_MS = 8 * 60 * 60 * 1000  # 8h, matches Element Call default
CALL_MEMBER_REFRESH_S = 60 * 60  # republish hourly to keep the membership fresh

SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLES_PER_FRAME = 480  # 10ms at 48kHz
BYTES_PER_FRAME = SAMPLES_PER_FRAME * CHANNELS * 2  # s16le = 2 bytes/sample

HELP_TEXT = """\
🎵 Music Bot Commands:
  !play <url|query>    — queue a YouTube/SoundCloud/Apple/Spotify URL or search
  !play <n>            — queue result #n from your last !search
  !play                — then send an audio file to queue it
  !search <query>      — show top YouTube hits to pick from
  !playlist <url> [N]  — queue the first N tracks of a playlist (default 50)
  !pl create <name>    — make a saved playlist
  !pl add <name> <url|query> — add a track to a saved playlist
  !pl play <name>      — queue everything from a saved playlist
  !pl shuffle <name>   — queue a saved playlist in random order
  !pl list             — list saved playlists
  !pl show <name>      — show tracks in a saved playlist
  !pl rm <name> <n>    — remove track #n from a saved playlist
  !pl delete <name>    — delete a saved playlist
  !skip                — skip the current track
  !stop                — stop playback and clear the queue
  !pause / !resume     — pause or resume
  !rewind [secs]       — jump back N seconds (default 15)
  !ff [secs]           — jump forward N seconds (default 15)
  !shuffle             — shuffle the current queue
  !queue               — show the current queue
  !np                  — show what's playing now
  !volume <0-200>      — set volume (100 = normal)\
"""

AUDIO_EXTENSIONS = {".mp3", ".ogg", ".flac", ".wav", ".m4a", ".opus", ".aac", ".wma"}

PENDING_UPLOAD_TTL = 300  # seconds before a `!play` (file) prompt expires
SEARCH_RESULTS_TTL = 600  # seconds before a `!search` selection expires
SEARCH_RESULTS_LIMIT = 5
PLAYLIST_DEFAULT_LIMIT = 50  # safety cap on auto-expansion to avoid 200-track queue floods
PLAYLISTS_PATH = STATE_DIR / "playlists.json"
SPOTIFY_URL_RE = re.compile(r"open\.spotify\.com/(playlist|album|track)/([A-Za-z0-9]+)")
APPLE_SONG_URL_RE = re.compile(r"music\.apple\.com/[^/]+/song/[^/]+/(\d+)")
APPLE_ALBUM_URL_RE = re.compile(r"music\.apple\.com/[^/]+/album/[^/]+/(\d+)")
APPLE_PLAYLIST_URL_RE = re.compile(r"music\.apple\.com/[^/]+/playlist/")
APPLE_I_PARAM_RE = re.compile(r"[?&]i=(\d+)")
SOUNDCLOUD_SET_RE = re.compile(r"soundcloud\.com/[^/]+/sets/")


def is_playlist_url(url: str) -> bool:
    """A URL is a playlist if it explicitly points at one — `/playlist?` in the path,
    or `list=` without a `v=` (so `watch?v=X&list=Y` stays a single video)."""
    if "/playlist" in url:
        return True
    if re.search(r"[?&]list=", url) and not re.search(r"[?&]v=", url):
        return True
    spot = SPOTIFY_URL_RE.search(url)
    if spot and spot.group(1) in ("playlist", "album"):
        return True
    if SOUNDCLOUD_SET_RE.search(url):
        return True
    if APPLE_PLAYLIST_URL_RE.search(url):
        return True
    if APPLE_ALBUM_URL_RE.search(url) and not APPLE_I_PARAM_RE.search(url):
        return True
    return False


def is_spotify_track_url(url: str) -> bool:
    spot = SPOTIFY_URL_RE.search(url)
    return bool(spot and spot.group(1) == "track")


def is_apple_music_url(url: str) -> bool:
    return "music.apple.com/" in url

_YT_PLAYLIST_PARAMS = re.compile(r"&(?:list|index|start_radio|playlist)=[^&]+")


def strip_playlist_params(url: str) -> str:
    """Drop YouTube playlist/radio params so yt-dlp grabs only the single video."""
    return _YT_PLAYLIST_PARAMS.sub("", url)


def _yt_flat_thumbnail(info: dict) -> Optional[str]:
    """Pull a thumbnail URL from a yt-dlp entry. `--flat-playlist` entries carry a
    `thumbnails` list (last = highest res) rather than a single `thumbnail`."""
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = info.get("thumbnails")
    if isinstance(thumbs, list) and thumbs:
        return thumbs[-1].get("url")
    return None


@dataclass
class Track:
    title: str
    source: str         # local file path or `yt://<url>`
    cleanup_path: Optional[str] = None  # tempfile to unlink after playback
    seek: float = 0.0   # seconds to skip from the start (for !rewind / !ff replays)
    duration: Optional[float] = None    # seconds, when the source exposes it
    thumbnail: Optional[str] = None     # artwork URL, when available
    uploader: Optional[str] = None      # channel / uploader name, when available


@dataclass
class RoomState:
    queue: deque = field(default_factory=deque)
    current: Optional[Track] = None
    paused: bool = False
    volume: float = 1.0
    lk_room: Optional[rtc.Room] = None
    audio_source: Optional[rtc.AudioSource] = None
    playback_task: Optional[asyncio.Task] = None
    skip_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    call_member_task: Optional[asyncio.Task] = None
    play_started_at: float = 0.0   # monotonic clock when current ffmpeg began
    play_seek_offset: float = 0.0  # seek offset baked into the current ffmpeg invocation


class MusicBot:
    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        CRYPTO_STORE_DIR.mkdir(parents=True, exist_ok=True)

        # Re-use the saved device_id so the persisted megolm/olm sessions still match
        device_id = ""
        if CREDENTIALS_PATH.exists():
            device_id = json.loads(CREDENTIALS_PATH.read_text()).get("device_id", "")

        self.matrix = AsyncClient(
            MATRIX_HOMESERVER,
            MATRIX_USER,
            device_id=device_id,
            store_path=str(CRYPTO_STORE_DIR),
            config=AsyncClientConfig(encryption_enabled=True, store_sync_tokens=True),
        )
        self.rooms: dict[str, RoomState] = {}
        self._pending_upload: dict[str, tuple[str, float]] = {}  # sender -> (room_id, expires_at)
        self._decrypt_warned: set[str] = set()  # room_ids we've already warned about E2EE failure
        # sender -> (results: list[(title, url)], expires_at)
        self._search_results: dict[str, tuple[list[tuple[str, str]], float]] = {}
        self._saved_playlists: dict[str, list[dict]] = self._load_playlists()
        self._spotify_token: Optional[tuple[str, float]] = None  # (token, expires_monotonic)
        # The voice room is where the LiveKit call lives — audio streams there
        # Commands come from any room (music room) but playback targets here
        self.voice_room_id: Optional[str] = os.environ.get("VOICE_ROOM_ID")

    # ── helpers ──────────────────────────────────────────────────────────────

    def state(self, room_id: str) -> RoomState:
        if room_id not in self.rooms:
            self.rooms[room_id] = RoomState()
        return self.rooms[room_id]

    async def send(self, room_id: str, text: str):
        # In encrypted rooms matrix-nio needs an active megolm group session before
        # it'll encrypt outgoing messages — otherwise they go out as plaintext.
        room = self.matrix.rooms.get(room_id)
        if room is not None and room.encrypted and self.matrix.should_share_group_session(room_id):
            try:
                await self.matrix.share_group_session(room_id, ignore_unverified_devices=True)
            except Exception as e:
                log.warning(f"Failed to share group session in {room_id}: {e}")
        await self.matrix.room_send(
            room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": text},
            ignore_unverified_devices=True,
        )

    # ── LiveKit ───────────────────────────────────────────────────────────────

    async def _find_active_livekit_room(self) -> Optional[str]:
        """Query LiveKit directly for a room that has participants."""
        from livekit.api import LiveKitAPI, ListRoomsRequest
        lk_http_url = LIVEKIT_URL.replace("wss://", "https://").replace("ws://", "http://")
        async with LiveKitAPI(lk_http_url, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) as lk:
            result = await lk.room.list_rooms(ListRoomsRequest())
        active = [r for r in result.rooms if r.num_participants > 0]
        if not active:
            return None
        best = max(active, key=lambda r: r.num_participants)
        log.info(f"Found active LiveKit room: {best.name} ({best.num_participants} participants)")
        return best.name

    def _call_member_state_key(self) -> str:
        return f"_{self.matrix.user_id}_{self.matrix.device_id}_m.call"

    def _call_member_content(self, matrix_room_id: str) -> dict:
        return {
            "application": "m.call",
            "call_id": "",
            "created_ts": int(time.time() * 1000),
            "device_id": self.matrix.device_id,
            "expires": CALL_MEMBER_EXPIRES_MS,
            "foci_preferred": [{
                "livekit_alias": matrix_room_id,
                "livekit_service_url": LIVEKIT_SERVICE_URL,
                "type": "livekit",
            }],
            "focus_active": {"focus_selection": "oldest_membership", "type": "livekit"},
            "m.call.intent": "audio",
            "scope": "m.room",
        }

    async def _publish_call_member(self, matrix_room_id: str):
        await self.matrix.room_put_state(
            matrix_room_id, CALL_MEMBER_TYPE,
            self._call_member_content(matrix_room_id),
            state_key=self._call_member_state_key(),
        )

    async def _clear_call_member(self, matrix_room_id: str):
        try:
            await self.matrix.room_put_state(
                matrix_room_id, CALL_MEMBER_TYPE, {},
                state_key=self._call_member_state_key(),
            )
        except Exception as e:
            log.warning(f"Failed to clear call.member state: {e}")

    async def _refresh_call_member_loop(self, matrix_room_id: str):
        try:
            while True:
                await asyncio.sleep(CALL_MEMBER_REFRESH_S)
                try:
                    await self._publish_call_member(matrix_room_id)
                    log.info(f"Refreshed call.member state in {matrix_room_id}")
                except Exception as e:
                    log.warning(f"call.member refresh failed: {e}")
        except asyncio.CancelledError:
            pass

    async def ensure_livekit(self, room_id: str):
        s = self.state(room_id)
        if s.lk_room and s.lk_room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
            return

        lk_room_name = await self._find_active_livekit_room()
        if not lk_room_name:
            raise RuntimeError("No active voice call found — start a call first, then use !play.")

        # Element Call only subscribes to participants whose identity matches
        # `@user:domain:devicesuffix` — a bare "music-bot" identity gets filtered out.
        bot_identity = f"{self.matrix.user_id}:{(self.matrix.device_id or 'MUSICBOT')[:8]}"
        token = (
            api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            .with_identity(bot_identity)
            .with_name("🎵 Music Bot")
            .with_grants(api.VideoGrants(room_join=True, room=lk_room_name))
            .to_jwt()
        )

        s.lk_room = rtc.Room()
        s.audio_source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
        await s.lk_room.connect(LIVEKIT_URL, token)

        track = rtc.LocalAudioTrack.create_audio_track("music", s.audio_source)
        await s.lk_room.local_participant.publish_track(
            track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        log.info(f"Connected to LiveKit room: {lk_room_name}")

        # Element Call clients only render LiveKit participants that have a matching
        # `org.matrix.msc3401.call.member` state event in the Matrix room.
        try:
            await self._publish_call_member(room_id)
            log.info(f"Published call.member state in {room_id}")
        except Exception as e:
            log.error(f"Failed to publish call.member state: {e}")
        if s.call_member_task is None or s.call_member_task.done():
            s.call_member_task = asyncio.create_task(self._refresh_call_member_loop(room_id))

    async def stream_audio(self, s: RoomState, source: str, seek: float = 0.0):
        """Pipe audio from source (local file or yt://<url>) through FFmpeg into LiveKit.
        `seek` skips that many seconds from the start (used by !rewind / !ff replays)."""
        is_youtube = source.startswith("yt://")
        yt_url = strip_playlist_params(source[5:]) if is_youtube else None
        ffmpeg_proc = None
        yt_proc = None
        pipe_r = pipe_w = None

        try:
            if is_youtube:
                # OS-level pipe between yt-dlp stdout and ffmpeg stdin — asyncio's
                # StreamReader can't be reused as another subprocess's stdin.
                pipe_r, pipe_w = os.pipe()
                log.info(f"yt-dlp streaming {yt_url} to ffmpeg")
                yt_proc = await asyncio.create_subprocess_exec(
                    "yt-dlp", "-q", "--no-warnings", "--no-playlist",
                    "-f", "bestaudio/best",
                    "-o", "-", "--", yt_url,
                    stdout=pipe_w,
                    stderr=asyncio.subprocess.PIPE,
                )
                os.close(pipe_w)
                pipe_w = None
                ffmpeg_input = "pipe:0"
                ffmpeg_stdin = pipe_r
            else:
                ffmpeg_input = source
                ffmpeg_stdin = None

            cmd = ["ffmpeg", "-re"]
            if seek > 0:
                # `-ss` before `-i` does demuxer-level seek when the input is seekable
                # (local files); over a pipe ffmpeg falls back to decode-and-discard.
                cmd += ["-ss", f"{seek:.3f}"]
            cmd += [
                "-i", ffmpeg_input,
                "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                "pipe:1",
            ]
            log.info(f"Starting FFmpeg: {' '.join(cmd)}")
            ffmpeg_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=ffmpeg_stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if pipe_r is not None:
                os.close(pipe_r)
                pipe_r = None

            frames_captured = 0
            bytes_read = 0
            audio_frame = rtc.AudioFrame.create(SAMPLE_RATE, CHANNELS, SAMPLES_PER_FRAME)
            frame_data = audio_frame.data

            while not s.stop_event.is_set() and not s.skip_event.is_set():
                if s.paused:
                    await asyncio.sleep(0.05)
                    continue

                data = await ffmpeg_proc.stdout.read(BYTES_PER_FRAME)
                if not data:
                    _, ffmpeg_stderr = await ffmpeg_proc.communicate()
                    if ffmpeg_stderr:
                        last_lines = ffmpeg_stderr.decode(errors='replace')[-500:]
                        log.error(f"FFmpeg stderr: {last_lines}")
                    break

                bytes_read += len(data)
                if len(data) < BYTES_PER_FRAME:
                    data = data.ljust(BYTES_PER_FRAME, b"\x00")
                if s.volume != 1.0:
                    samples = array.array("h")
                    samples.frombytes(data)
                    vol = s.volume
                    for i, sample in enumerate(samples):
                        scaled = int(sample * vol)
                        if scaled > 32767:
                            scaled = 32767
                        elif scaled < -32768:
                            scaled = -32768
                        samples[i] = scaled
                    data = samples.tobytes()
                frame_data.cast('B')[:len(data)] = data
                await s.audio_source.capture_frame(audio_frame)
                frames_captured += 1
                if frames_captured % 500 == 0:
                    log.info(f"Streaming: {frames_captured} frames, {bytes_read} bytes read")

            log.info(f"FFmpeg ended: {frames_captured} frames captured, {bytes_read} bytes read")

        finally:
            for fd in (pipe_r, pipe_w):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            for proc in (ffmpeg_proc, yt_proc):
                if proc:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()
            if yt_proc and yt_proc.returncode not in (0, None):
                stderr = await yt_proc.stderr.read() if yt_proc.stderr else b""
                if stderr:
                    log.error(f"yt-dlp stderr: {stderr.decode(errors='replace')[-500:]}")

    async def playback_loop(self, room_id: str, state_room: str):
        # room_id: where to send messages (music room)
        # state_room: where to find queue/LiveKit state (voice room)
        s = self.state(state_room)
        try:
            while not s.stop_event.is_set():
                if not s.queue:
                    s.current = None
                    await asyncio.sleep(0.5)
                    continue

                track = s.queue.popleft()
                s.current = track
                s.skip_event.clear()
                s.play_seek_offset = track.seek
                s.play_started_at = time.monotonic()

                # Suppress the announcement on a !rewind/!ff replay since the user just
                # asked for it — only announce on the initial play.
                if track.seek == 0:
                    await self.send(room_id, f"▶️ Now playing: **{track.title}**")
                try:
                    await self.stream_audio(s, track.source, seek=track.seek)
                except Exception as e:
                    log.error(f"Playback error in {room_id}: {e}")
                    await self.send(room_id, f"⚠️ Error playing: {track.title}")
                finally:
                    # Don't unlink the local file if the same track is queued again
                    # (e.g. !rewind prepended a copy that still needs the file).
                    still_needed = any(t.cleanup_path == track.cleanup_path for t in s.queue)
                    if track.cleanup_path and not still_needed and os.path.exists(track.cleanup_path):
                        try:
                            os.unlink(track.cleanup_path)
                        except OSError as e:
                            log.warning(f"Failed to unlink {track.cleanup_path}: {e}")
        finally:
            s.current = None
            if s.call_member_task and not s.call_member_task.done():
                s.call_member_task.cancel()
            await self._clear_call_member(state_room)
            if s.lk_room:
                await s.lk_room.disconnect()
                s.lk_room = None
            log.info(f"Playback ended in {room_id}")

    async def ensure_playback(self, room_id: str):
        # Commands come from any room (music room), but audio streams to the voice room
        target_room = self.voice_room_id or room_id
        s = self.state(target_room)
        async with s.start_lock:
            if s.playback_task is None or s.playback_task.done():
                await self.ensure_livekit(target_room)
                s.stop_event.clear()
                s.playback_task = asyncio.create_task(self.playback_loop(room_id, target_room))

    # ── audio sources ─────────────────────────────────────────────────────────

    # ── Spotify ───────────────────────────────────────────────────────────────

    async def _spotify_get_token(self) -> Optional[str]:
        if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
            return None
        if self._spotify_token and time.monotonic() < self._spotify_token[1]:
            return self._spotify_token[0]
        async with aiohttp.ClientSession() as sess:
            auth = aiohttp.BasicAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
            async with sess.post(
                "https://accounts.spotify.com/api/token",
                auth=auth,
                data={"grant_type": "client_credentials"},
            ) as resp:
                if resp.status != 200:
                    log.error(f"Spotify token request failed: {resp.status}")
                    return None
                data = await resp.json()
        token = data["access_token"]
        self._spotify_token = (token, time.monotonic() + data.get("expires_in", 3600) - 60)
        return token

    async def _spotify_fetch(self, path: str, token: str, params: Optional[dict] = None) -> Optional[dict]:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                f"https://api.spotify.com/v1{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            ) as resp:
                if resp.status != 200:
                    log.error(f"Spotify GET {path} failed: {resp.status} — {await resp.text()}")
                    return None
                return await resp.json()

    async def _spotify_resolve(self, kind: str, sid: str, limit: int) -> list[tuple[str, str]]:
        """Return [(title, artist), ...] for a Spotify track/album/playlist URL."""
        token = await self._spotify_get_token()
        if not token:
            return []

        if kind == "track":
            data = await self._spotify_fetch(f"/tracks/{sid}", token)
            if not data:
                return []
            return [(data["name"], ", ".join(a["name"] for a in data["artists"]))]

        # album / playlist: paginate until we hit `limit` tracks
        if kind == "album":
            path = f"/albums/{sid}/tracks"
            extract = lambda item: (item["name"], ", ".join(a["name"] for a in item["artists"]))
        else:  # playlist
            path = f"/playlists/{sid}/tracks"
            extract = lambda item: (
                item["track"]["name"],
                ", ".join(a["name"] for a in item["track"]["artists"]),
            ) if item.get("track") else None

        out: list[tuple[str, str]] = []
        offset = 0
        while len(out) < limit:
            page_size = min(100, limit - len(out))
            data = await self._spotify_fetch(path, token, {"limit": page_size, "offset": offset})
            if not data or not data.get("items"):
                break
            for item in data["items"]:
                pair = extract(item)
                if pair:
                    out.append(pair)
                if len(out) >= limit:
                    break
            if not data.get("next"):
                break
            offset += page_size
        return out

    # ── Apple Music ───────────────────────────────────────────────────────────

    @staticmethod
    def _apple_track_query(track: dict) -> Optional[tuple[str, str]]:
        title = track.get("trackName") or ""
        artist = track.get("artistName") or ""
        if not title:
            return None
        return title, artist

    async def _apple_music_resolve(self, url: str, limit: int) -> tuple[list[tuple[str, str]], Optional[str]]:
        """Return ([(title, artist), ...], error) for Apple Music song/album URLs.

        Apple user playlists are not exposed through the free iTunes Lookup API,
        but albums and individual songs are.
        """
        if APPLE_PLAYLIST_URL_RE.search(url):
            return [], "Apple Music user playlists aren't supported. Use an album URL instead."

        i_match = APPLE_I_PARAM_RE.search(url)
        if i_match:
            lookup_id = i_match.group(1)
            kind = "song"
        else:
            song_match = APPLE_SONG_URL_RE.search(url)
            album_match = APPLE_ALBUM_URL_RE.search(url)
            if song_match:
                lookup_id = song_match.group(1)
                kind = "song"
            elif album_match:
                lookup_id = album_match.group(1)
                kind = "album"
            else:
                return [], "Unrecognized Apple Music URL."

        params = {"id": lookup_id}
        if kind == "album":
            params["entity"] = "song"

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as sess:
                async with sess.get("https://itunes.apple.com/lookup", params=params) as resp:
                    if resp.status != 200:
                        return [], "Couldn't reach Apple Music."
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return [], "Couldn't reach Apple Music."

        tracks = [
            r for r in data.get("results", [])
            if isinstance(r, dict) and r.get("wrapperType") == "track"
        ]
        if kind == "song":
            if not tracks:
                return [], "Couldn't read that Apple Music song."
            pair = self._apple_track_query(tracks[0])
            return ([pair] if pair else []), None if pair else "Couldn't read that Apple Music song."

        entries = [p for p in (self._apple_track_query(t) for t in tracks) if p]
        if not entries:
            return [], "Couldn't load that Apple Music album."
        return entries[:limit], None

    async def _yt_playlist(self, url: str, limit: int) -> list[Track]:
        """Resolve a playlist URL into a list of Tracks (no per-video re-fetch — uses
        flat-playlist metadata so we don't pay an HTTP roundtrip per entry)."""
        log.info(f"Resolving playlist: {url} (limit {limit})")
        try:
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp", "-q", "--no-warnings",
                "--flat-playlist", "--dump-json",
                "-I", f"1:{limit}",
                "--", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            if proc.returncode != 0:
                log.error(f"yt-dlp playlist failed: {stderr.decode(errors='replace')[-500:]}")
                return []
            tracks = []
            for line in stdout.decode().splitlines():
                if not line.strip():
                    continue
                info = json.loads(line)
                entry_url = info.get("url") or info.get("webpage_url")
                if not entry_url:
                    continue
                tracks.append(Track(
                    title=info.get("title", entry_url),
                    source=f"yt://{entry_url}",
                    duration=info.get("duration"),
                    thumbnail=_yt_flat_thumbnail(info),
                    uploader=info.get("uploader") or info.get("channel"),
                ))
            return tracks
        except asyncio.TimeoutError:
            log.error(f"yt-dlp playlist timed out for {url}")
            return []
        except Exception as e:
            log.error(f"yt-dlp playlist error for {url}: {e}", exc_info=True)
            return []

    async def _yt_search(self, query: str, limit: int) -> list[dict]:
        """Return [{title, url, duration, uploader, thumbnail}, ...] for the top
        `limit` YouTube hits."""
        log.info(f"Searching YouTube for: {query!r} (limit {limit})")
        try:
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp", "-q", "--no-warnings", "--flat-playlist",
                "--dump-json", f"ytsearch{limit}:{query}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            if proc.returncode != 0:
                log.error(f"yt-dlp search failed: {stderr.decode(errors='replace')[-500:]}")
                return []
            results = []
            for line in stdout.decode().splitlines():
                if not line.strip():
                    continue
                info = json.loads(line)
                url = info.get("url") or info.get("webpage_url")
                if not url:
                    continue
                results.append({
                    "title": info.get("title", url),
                    "url": url,
                    "duration": info.get("duration"),
                    "uploader": info.get("uploader") or info.get("channel"),
                    "thumbnail": _yt_flat_thumbnail(info),
                })
            return results
        except asyncio.TimeoutError:
            log.error(f"yt-dlp search timed out for {query!r}")
            return []
        except Exception as e:
            log.error(f"yt-dlp search error for {query!r}: {e}", exc_info=True)
            return []

    async def _yt_resolve(self, url: str) -> Optional[Track]:
        log.info(f"Resolving URL: {url}")
        try:
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp", "-f", "bestaudio/best", "-q", "--no-warnings",
                "--dump-json", "--no-playlist", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            if proc.returncode != 0:
                log.error(f"yt-dlp failed: {stderr.decode()}")
                return None
            info = json.loads(stdout.decode())
            log.info(f"Resolved: {info.get('title', url)}")
            return Track(
                title=info.get("title", url),
                source=f"yt://{url}",
                duration=info.get("duration"),
                thumbnail=info.get("thumbnail"),
                uploader=info.get("uploader") or info.get("channel"),
            )
        except asyncio.TimeoutError:
            log.error(f"yt-dlp timed out for {url}")
            return None
        except Exception as e:
            log.error(f"yt-dlp error for {url}: {e}", exc_info=True)
            return None

    async def _fetch_mxc_bytes(self, mxc_url: str) -> Optional[bytes]:
        """Raw download of an mxc:// URI. Returns the encrypted bytes for E2EE files."""
        server, media_id = mxc_url[6:].split("/", 1)
        http_url = f"{MATRIX_HOMESERVER}/_matrix/media/v3/download/{server}/{media_id}"
        async with aiohttp.ClientSession() as sess:
            headers = {"Authorization": f"Bearer {self.matrix.access_token}"}
            async with sess.get(http_url, headers=headers) as resp:
                if resp.status != 200:
                    log.error(f"MXC download failed: {resp.status} for {mxc_url}")
                    return None
                return await resp.read()

    async def _download_attachment(self, event, filename: str) -> Optional[str]:
        """Download a Matrix audio/file attachment to a temp file. Handles E2EE files,
        whose ciphertext + key/iv/hashes live under `content.file` instead of `url`."""
        encrypted_info = event.source.get("content", {}).get("file") if event.source else None
        mxc_url = event.url or (encrypted_info.get("url") if encrypted_info else None)
        if not mxc_url:
            log.error(f"Attachment {filename} has no mxc URL")
            return None

        data = await self._fetch_mxc_bytes(mxc_url)
        if data is None:
            return None

        if encrypted_info:
            try:
                data = decrypt_attachment(
                    data,
                    encrypted_info["key"]["k"],
                    encrypted_info["hashes"]["sha256"],
                    encrypted_info["iv"],
                )
            except Exception as e:
                log.error(f"Failed to decrypt attachment {filename}: {e}")
                return None

        ext = os.path.splitext(filename)[1] or ".audio"
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        try:
            tmp.write(data)
        finally:
            tmp.close()
        return tmp.name

    # ── event handlers ────────────────────────────────────────────────────────

    async def on_sync(self, response: SyncResponse):
        for room_id in response.rooms.invite:
            log.info(f"Accepting invite to {room_id}")
            try:
                await self.matrix.join(room_id)
            except Exception as e:
                log.warning(f"Join failed for {room_id}: {e}")
                continue
            # Greeting can race with the local room cache populating after join — best-effort.
            try:
                await self.send(room_id, "👋 Hi! I'm your music bot. Type `!help` to see what I can do.")
            except Exception as e:
                log.warning(f"Greeting failed for {room_id}: {e}")

    async def on_message(self, room: MatrixRoom, event: RoomMessageText):
        if event.sender == self.matrix.user_id:
            return
        body = event.body.strip()
        room_id = room.room_id
        # Use voice room state for all playback control (music streams there)
        target_room = self.voice_room_id or room_id
        s = self.state(target_room)

        if body == "!help":
            await self.send(room_id, HELP_TEXT)

        elif body.startswith("!play "):
            arg = body[6:].strip()
            url = self._resolve_play_arg(event.sender, arg)
            if url is None:
                await self.send(room_id, "❌ That number isn't in your last !search results.")
                return
            await self.send(room_id, "🔍 Looking up…")
            try:
                if is_playlist_url(url):
                    await self._queue_playlist(room_id, s, url, PLAYLIST_DEFAULT_LIMIT)
                    return
                if is_spotify_track_url(url):
                    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
                        await self.send(room_id, "❌ Spotify support needs SPOTIFY_CLIENT_ID/SECRET to be set.")
                        return
                    spot = SPOTIFY_URL_RE.search(url)
                    entries = await self._spotify_resolve("track", spot.group(2), 1)
                    if not entries:
                        await self.send(room_id, "❌ Couldn't load that Spotify track.")
                        return
                    tracks = await self._tracks_from_search_pairs(entries)
                    s.queue.append(tracks[0])
                    await self.send(room_id, f"➕ Queued: **{tracks[0].title}**")
                    await self.ensure_playback(room_id)
                    return
                if is_apple_music_url(url):
                    entries, err = await self._apple_music_resolve(url, 1)
                    if err or not entries:
                        msg = err or "Couldn't load that Apple Music URL."
                        await self.send(room_id, f"❌ {msg}")
                        return
                    tracks = await self._tracks_from_search_pairs(entries)
                    s.queue.append(tracks[0])
                    await self.send(room_id, f"➕ Queued: **{tracks[0].title}**")
                    await self.ensure_playback(room_id)
                    return
                track = await self._yt_resolve(url)
                if not track:
                    await self.send(room_id, "❌ Couldn't load that URL.")
                    return
                s.queue.append(track)
                pos = len(s.queue)
                await self.send(room_id, f"➕ Queued #{pos}: **{track.title}**")
                await self.ensure_playback(room_id)
            except Exception as e:
                log.error(f"!play error: {e}", exc_info=True)
                await self.send(room_id, f"❌ Error: {e}")

        elif body == "!pl" or body.startswith("!pl "):
            await self._handle_pl(room_id, s, body)

        elif body.startswith("!playlist "):
            parts = body[10:].strip().split()
            if not parts:
                await self.send(room_id, "Usage: `!playlist <url> [N]`")
                return
            url = parts[0]
            limit = PLAYLIST_DEFAULT_LIMIT
            if len(parts) > 1:
                try:
                    limit = max(1, int(parts[1]))
                except ValueError:
                    await self.send(room_id, "Usage: `!playlist <url> [N]`")
                    return
            await self.send(room_id, f"📜 Loading playlist (up to {limit})…")
            try:
                await self._queue_playlist(room_id, s, url, limit)
            except Exception as e:
                log.error(f"!playlist error: {e}", exc_info=True)
                await self.send(room_id, f"❌ Error: {e}")

        elif body.startswith("!search "):
            query = body[8:].strip()
            if not query:
                await self.send(room_id, "Usage: `!search <query>`")
                return
            await self.send(room_id, f"🔎 Searching YouTube for **{query}**…")
            results = await self._yt_search(query, SEARCH_RESULTS_LIMIT)
            if not results:
                await self.send(room_id, "❌ No results.")
                return
            pairs = [(r["title"], r["url"]) for r in results]
            self._search_results[event.sender] = (pairs, time.monotonic() + SEARCH_RESULTS_TTL)
            lines = [f"  {i}. {title}" for i, (title, _) in enumerate(pairs, 1)]
            lines.append("Reply with `!play <n>` to queue one.")
            await self.send(room_id, "\n".join(lines))

        elif body == "!play":
            self._pending_upload[event.sender] = (room_id, time.monotonic() + PENDING_UPLOAD_TTL)
            await self.send(room_id, "📎 Send an audio file now and I'll queue it.")

        elif body == "!skip":
            s.skip_event.set()
            await self.send(room_id, "⏭️ Skipped.")

        elif body == "!stop":
            s.stop_event.set()
            s.skip_event.set()
            s.queue.clear()
            await self.send(room_id, "⏹️ Stopped and queue cleared.")

        elif body == "!pause":
            s.paused = True
            await self.send(room_id, "⏸️ Paused.")

        elif body == "!resume":
            s.paused = False
            await self.send(room_id, "▶️ Resumed.")

        elif body == "!rewind" or body.startswith("!rewind ") or body == "!ff" or body.startswith("!ff "):
            cmd = "!rewind" if body.startswith("!rewind") else "!ff"
            arg = body[len(cmd):].strip()
            try:
                secs = float(arg) if arg else 15.0
                if secs <= 0:
                    raise ValueError
            except ValueError:
                await self.send(room_id, f"Usage: `{cmd} [seconds]`")
                return
            await self._seek_relative(room_id, s, -secs if cmd == "!rewind" else secs)

        elif body == "!np":
            if s.current:
                await self.send(room_id, f"▶️ Now playing: **{s.current.title}**")
            else:
                await self.send(room_id, "Nothing is playing.")

        elif body == "!shuffle":
            if not s.queue:
                await self.send(room_id, "Queue is empty — nothing to shuffle.")
                return
            items = list(s.queue)
            random.shuffle(items)
            s.queue.clear()
            s.queue.extend(items)
            await self.send(room_id, f"🔀 Shuffled {len(items)} track(s).")

        elif body == "!queue":
            if not s.current and not s.queue:
                await self.send(room_id, "The queue is empty.")
                return
            lines = []
            if s.current:
                lines.append(f"▶️ {s.current.title}")
            for i, t in enumerate(s.queue, 1):
                lines.append(f"  {i}. {t.title}")
            await self.send(room_id, "\n".join(lines))

        elif body.startswith("!volume "):
            try:
                pct = int(body[8:].strip())
                if not 0 <= pct <= 200:
                    raise ValueError
                s.volume = pct / 100.0
                await self.send(room_id, f"🔊 Volume set to {pct}%.")
            except ValueError:
                await self.send(room_id, "Usage: `!volume <0-200>`")

    # ── saved playlists ───────────────────────────────────────────────────────

    def _load_playlists(self) -> dict[str, list[dict]]:
        if not PLAYLISTS_PATH.exists():
            return {}
        try:
            return json.loads(PLAYLISTS_PATH.read_text())
        except Exception as e:
            log.warning(f"Failed to load saved playlists: {e}")
            return {}

    def _save_playlists(self):
        try:
            PLAYLISTS_PATH.write_text(json.dumps(self._saved_playlists, indent=2))
        except Exception as e:
            log.error(f"Failed to persist saved playlists: {e}")

    async def _resolve_pl_entry(self, arg: str) -> Optional[dict]:
        """Resolve `!pl add` arg (URL or search query) to a {title, source} dict."""
        if arg.startswith("http://") or arg.startswith("https://"):
            if is_spotify_track_url(arg):
                if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
                    return None
                spot = SPOTIFY_URL_RE.search(arg)
                entries = await self._spotify_resolve("track", spot.group(2), 1)
                if not entries:
                    return None
                tracks = await self._tracks_from_search_pairs(entries)
                return {"title": tracks[0].title, "source": tracks[0].source}
            if is_apple_music_url(arg):
                entries, _err = await self._apple_music_resolve(arg, 1)
                if not entries:
                    return None
                tracks = await self._tracks_from_search_pairs(entries)
                return {"title": tracks[0].title, "source": tracks[0].source}
            track = await self._yt_resolve(arg)
            return {"title": track.title, "source": track.source} if track else None
        # Treat as search query — resolve so we save a real title
        track = await self._yt_resolve(f"ytsearch1:{arg}")
        return {"title": track.title, "source": track.source} if track else None

    async def _handle_pl(self, room_id: str, s: RoomState, body: str):
        parts = body.split(maxsplit=2)
        if len(parts) < 2:
            await self.send(room_id, "Usage: `!pl <create|add|play|list|show|rm|delete> ...`")
            return
        sub = parts[1]
        rest = parts[2] if len(parts) > 2 else ""

        if sub == "list":
            if not self._saved_playlists:
                await self.send(room_id, "No saved playlists.")
                return
            lines = [f"  {n} ({len(t)} tracks)" for n, t in sorted(self._saved_playlists.items())]
            await self.send(room_id, "Saved playlists:\n" + "\n".join(lines))
            return

        if sub == "create":
            name = rest.strip()
            if not name:
                await self.send(room_id, "Usage: `!pl create <name>`")
                return
            if name in self._saved_playlists:
                await self.send(room_id, f"Playlist **{name}** already exists.")
                return
            self._saved_playlists[name] = []
            self._save_playlists()
            await self.send(room_id, f"📜 Created playlist **{name}**.")
            return

        if sub == "delete":
            name = rest.strip()
            if name not in self._saved_playlists:
                await self.send(room_id, f"No playlist named **{name}**.")
                return
            del self._saved_playlists[name]
            self._save_playlists()
            await self.send(room_id, f"🗑️ Deleted playlist **{name}**.")
            return

        if sub == "show":
            name = rest.strip()
            entries = self._saved_playlists.get(name)
            if entries is None:
                await self.send(room_id, f"No playlist named **{name}**.")
                return
            if not entries:
                await self.send(room_id, f"**{name}** is empty.")
                return
            lines = [f"  {i}. {e['title']}" for i, e in enumerate(entries, 1)]
            await self.send(room_id, f"**{name}**:\n" + "\n".join(lines))
            return

        if sub == "add":
            sub_parts = rest.split(maxsplit=1)
            if len(sub_parts) < 2:
                await self.send(room_id, "Usage: `!pl add <name> <url|query>`")
                return
            name, query = sub_parts
            if name not in self._saved_playlists:
                await self.send(room_id, f"No playlist named **{name}**. Use `!pl create {name}` first.")
                return
            await self.send(room_id, "🔍 Looking up…")
            entry = await self._resolve_pl_entry(query)
            if not entry:
                await self.send(room_id, "❌ Couldn't resolve that.")
                return
            self._saved_playlists[name].append(entry)
            self._save_playlists()
            await self.send(room_id, f"➕ Added **{entry['title']}** to **{name}** ({len(self._saved_playlists[name])} total).")
            return

        if sub == "rm":
            sub_parts = rest.split(maxsplit=1)
            if len(sub_parts) < 2 or not sub_parts[1].isdigit():
                await self.send(room_id, "Usage: `!pl rm <name> <n>`")
                return
            name, idx_str = sub_parts
            entries = self._saved_playlists.get(name)
            if entries is None:
                await self.send(room_id, f"No playlist named **{name}**.")
                return
            idx = int(idx_str) - 1
            if not 0 <= idx < len(entries):
                await self.send(room_id, f"Track #{idx_str} doesn't exist in **{name}**.")
                return
            removed = entries.pop(idx)
            self._save_playlists()
            await self.send(room_id, f"➖ Removed **{removed['title']}** from **{name}**.")
            return

        if sub in ("play", "shuffle"):
            name = rest.strip()
            entries = self._saved_playlists.get(name)
            if entries is None:
                await self.send(room_id, f"No playlist named **{name}**.")
                return
            if not entries:
                await self.send(room_id, f"**{name}** is empty.")
                return
            ordered = list(entries)
            if sub == "shuffle":
                random.shuffle(ordered)
            for e in ordered:
                s.queue.append(Track(title=e["title"], source=e["source"]))
            verb = "🔀 Shuffled" if sub == "shuffle" else "➕ Queued"
            await self.send(room_id, f"{verb} {len(ordered)} track(s) from **{name}**.")
            await self.ensure_playback(room_id)
            return

        await self.send(room_id, f"Unknown `!pl` subcommand: {sub}")

    async def _resolve_playlist(self, url: str, limit: int) -> tuple[list[Track], Optional[str]]:
        """Resolve a playlist URL (YouTube/yt-dlp or Spotify) into Tracks.
        Returns (tracks, error_message); error_message is None on success."""
        spot = SPOTIFY_URL_RE.search(url)
        if spot:
            kind, sid = spot.group(1), spot.group(2)
            if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
                return [], "Spotify support needs SPOTIFY_CLIENT_ID/SECRET to be set."
            entries = await self._spotify_resolve(kind, sid, limit)
            if not entries:
                return [], "Couldn't load that Spotify URL (or it's empty/private)."
            return await self._tracks_from_search_pairs(entries), None
        if is_apple_music_url(url):
            entries, err = await self._apple_music_resolve(url, limit)
            if err or not entries:
                return [], err or "Couldn't load that Apple Music URL."
            return await self._tracks_from_search_pairs(entries), None
        tracks = await self._yt_playlist(url, limit)
        if not tracks:
            return [], "Couldn't load that playlist (or it's empty)."
        return tracks, None

    async def _queue_playlist(self, room_id: str, s: RoomState, url: str, limit: int):
        tracks, err = await self._resolve_playlist(url, limit)
        if err:
            await self.send(room_id, f"❌ {err}")
            return
        for t in tracks:
            s.queue.append(t)
        await self.send(room_id, f"➕ Queued {len(tracks)} track(s) from playlist.")
        await self.ensure_playback(room_id)

    async def _tracks_from_search_pairs(self, entries: list[tuple[str, str]]) -> list[Track]:
        """Convert (title, artist) pairs into Tracks via `ytsearch1:`."""
        tracks: list[Track] = []
        for title, artist in entries:
            query = f"{artist} {title}".strip()
            tracks.append(Track(title=f"{artist} — {title}".strip(" —"), source=f"yt://ytsearch1:{query}"))
        return tracks

    async def _seek_relative(self, room_id: str, s: RoomState, delta: float):
        """Restart the current track at (current_position + delta) seconds.
        Negative delta = rewind, positive = fast-forward."""
        track = s.current
        if not track:
            await self.send(room_id, "Nothing is playing.")
            return
        elapsed = s.play_seek_offset + max(0.0, time.monotonic() - s.play_started_at)
        new_seek = max(0.0, elapsed + delta)
        # Replay the same track from the new offset by prepending it to the queue
        # and signalling skip to break out of the current ffmpeg loop.
        replay = Track(
            title=track.title,
            source=track.source,
            cleanup_path=track.cleanup_path,
            seek=new_seek,
            duration=track.duration,
            thumbnail=track.thumbnail,
            uploader=track.uploader,
        )
        s.queue.appendleft(replay)
        s.skip_event.set()
        verb = "⏪ Rewound" if delta < 0 else "⏩ Skipped forward"
        await self.send(room_id, f"{verb} to {int(new_seek // 60)}:{int(new_seek % 60):02d}.")

    def _resolve_play_arg(self, sender: str, arg: str) -> Optional[str]:
        """Map a !play argument to a URL: a URL passes through, a number indexes the
        last !search results, and anything else becomes a ytsearch1: query.
        Returns None only if the number is out of range or the search expired."""
        if arg.startswith("http://") or arg.startswith("https://"):
            return arg
        if arg.isdigit():
            entry = self._search_results.get(sender)
            if not entry:
                return None
            results, expires_at = entry
            if time.monotonic() > expires_at:
                self._search_results.pop(sender, None)
                return None
            idx = int(arg) - 1
            if not 0 <= idx < len(results):
                return None
            return results[idx][1]
        return f"ytsearch1:{arg}"

    def _take_pending_upload(self, sender: str, room_id: str) -> bool:
        entry = self._pending_upload.pop(sender, None)
        if not entry:
            return False
        target, expires_at = entry
        if target != room_id or time.monotonic() > expires_at:
            return False
        return True

    async def _queue_audio_file(self, room_id: str, event, filename: str):
        await self.send(room_id, f"⬇️ Downloading **{filename}**…")
        path = await self._download_attachment(event, filename)
        if not path:
            await self.send(room_id, "❌ Couldn't download that file.")
            return
        track = Track(title=filename, source=path, cleanup_path=path)
        target_room = self.voice_room_id or room_id
        s = self.state(target_room)
        s.queue.append(track)
        pos = len(s.queue)
        await self.send(room_id, f"➕ Queued #{pos}: **{filename}**")
        await self.ensure_playback(room_id)

    async def on_audio(self, room: MatrixRoom, event: RoomMessageAudio):
        if event.sender == self.matrix.user_id:
            return
        if self._take_pending_upload(event.sender, room.room_id):
            await self._queue_audio_file(room.room_id, event, event.body)

    async def on_megolm(self, room: MatrixRoom, event: MegolmEvent):
        log.warning(f"Encrypted event in {room.room_id} — could not decrypt (no key or E2EE not set up)")
        if room.room_id in self._decrypt_warned:
            return
        self._decrypt_warned.add(room.room_id)
        try:
            await self.send(
                room.room_id,
                "🔒 I can't read encrypted messages here — verify my device or invite me before "
                "starting a key-shared session so I can pick up `!play` commands.",
            )
        except Exception as e:
            log.error(f"Failed to send decrypt-warning notice: {e}")

    async def on_file(self, room: MatrixRoom, event: RoomMessageFile):
        if event.sender == self.matrix.user_id:
            return
        ext = os.path.splitext(event.body)[1].lower()
        if ext not in AUDIO_EXTENSIONS:
            return
        if self._take_pending_upload(event.sender, room.room_id):
            await self._queue_audio_file(room.room_id, event, event.body)

    # ── web API surface ─────────────────────────────────────────────────────────
    # Thin, matrix-silent operations called by the in-process aiohttp API (web/).
    # Keeping all Track construction here means web/ never imports bot.py (which is
    # the __main__ entrypoint). User-fixable problems raise ValueError(message);
    # the web layer maps those to HTTP 4xx.

    @staticmethod
    def _track_dict(t: Track) -> dict:
        yt = t.source.startswith("yt://")
        return {
            "title": t.title,
            "url": t.source[5:] if yt else None,
            "source": "youtube" if yt else "local",
            "duration": t.duration,
            "uploader": t.uploader,
            "thumbnail": t.thumbnail,
            "requester": None,  # the bot doesn't attribute web/Matrix queues to a user
        }

    def _web_voice_state(self) -> RoomState:
        if not self.voice_room_id:
            raise ValueError("No VOICE_ROOM_ID configured — set it to control playback from the web.")
        return self.state(self.voice_room_id)

    async def _try_start_playback(self, room_id: str) -> bool:
        """Start the playback loop if a LiveKit call is active. Returns whether the
        bot is connected. Swallows the no-active-call case so web enqueues still
        land in the queue (they play once a call exists and playback is kicked)."""
        try:
            await self.ensure_playback(room_id)
        except RuntimeError as e:
            log.info(f"Queued but not playing: {e}")
            return False
        s = self.state(room_id)
        return bool(s.lk_room and s.lk_room.connection_state == rtc.ConnectionState.CONN_CONNECTED)

    def web_now_playing(self) -> dict:
        if not self.voice_room_id:
            return {"configured": False, "connected": False, "channel": None, "paused": False,
                    "volume": 100, "current": None, "queue": [], "elapsed": None,
                    "duration": None}
        s = self.state(self.voice_room_id)
        connected = bool(s.lk_room and s.lk_room.connection_state == rtc.ConnectionState.CONN_CONNECTED)
        elapsed = None
        if s.current:
            elapsed = s.play_seek_offset + max(0.0, time.monotonic() - s.play_started_at)
        return {
            "configured": True,
            "connected": connected,
            "channel": None,  # single-room: the LiveKit alias mirrors VOICE_ROOM_ID
            "paused": s.paused,
            "volume": int(round(s.volume * 100)),
            "current": self._track_dict(s.current) if s.current else None,
            "queue": [self._track_dict(t) for t in s.queue],
            "elapsed": elapsed,
            "duration": s.current.duration if s.current else None,
        }

    async def web_search(self, query: str, limit: int) -> list[dict]:
        results = await self._yt_search(query, limit)
        for r in results:
            r["source"] = "youtube"
        return results

    async def web_enqueue(self, query: str) -> dict:
        """Resolve a URL/search query and queue it. Mirrors `!play` (incl. playlist
        and Spotify-track auto-detection) but returns structured data."""
        s = self._web_voice_state()
        query = (query or "").strip()
        if not query:
            raise ValueError("query required.")
        is_url = query.startswith("http://") or query.startswith("https://")
        if is_url and is_playlist_url(query):
            tracks, err = await self._resolve_playlist(query, PLAYLIST_DEFAULT_LIMIT)
            if err:
                raise ValueError(err)
        elif is_url and is_spotify_track_url(query):
            if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
                raise ValueError("Spotify support needs SPOTIFY_CLIENT_ID/SECRET to be set.")
            spot = SPOTIFY_URL_RE.search(query)
            entries = await self._spotify_resolve("track", spot.group(2), 1)
            if not entries:
                raise ValueError("Couldn't load that Spotify track.")
            tracks = await self._tracks_from_search_pairs(entries)
        elif is_url and is_apple_music_url(query):
            entries, err = await self._apple_music_resolve(query, 1)
            if err or not entries:
                raise ValueError(err or "Couldn't load that Apple Music URL.")
            tracks = await self._tracks_from_search_pairs(entries)
        else:
            track = await self._yt_resolve(query if is_url else f"ytsearch1:{query}")
            if not track:
                raise ValueError("Couldn't resolve that query.")
            tracks = [track]
        for t in tracks:
            s.queue.append(t)
        connected = await self._try_start_playback(self.voice_room_id)
        return {"queued": len(tracks), "connected": connected,
                "tracks": [self._track_dict(t) for t in tracks]}

    async def web_enqueue_playlist(self, url: str, limit: int) -> dict:
        s = self._web_voice_state()
        tracks, err = await self._resolve_playlist(url, limit)
        if err:
            raise ValueError(err)
        for t in tracks:
            s.queue.append(t)
        connected = await self._try_start_playback(self.voice_room_id)
        return {"queued": len(tracks), "connected": connected}

    async def web_playback(self, action: str) -> dict:
        s = self._web_voice_state()
        if action == "pause":
            s.paused = True
        elif action == "resume":
            s.paused = False
        elif action == "skip":
            s.skip_event.set()
        elif action == "stop":
            s.stop_event.set()
            s.skip_event.set()
            s.queue.clear()
        elif action == "shuffle":
            items = list(s.queue)
            random.shuffle(items)
            s.queue.clear()
            s.queue.extend(items)
        else:
            raise ValueError(f"Unknown action: {action!r}.")
        return {"ok": True}

    async def web_seek(self, delta: float) -> dict:
        """Relative seek by `delta` seconds (negative = rewind). Silent twin of
        `_seek_relative`: prepend a replay of the current track and break ffmpeg."""
        s = self._web_voice_state()
        track = s.current
        if not track:
            raise ValueError("Nothing is playing.")
        elapsed = s.play_seek_offset + max(0.0, time.monotonic() - s.play_started_at)
        new_seek = max(0.0, elapsed + delta)
        s.queue.appendleft(Track(title=track.title, source=track.source,
                                 cleanup_path=track.cleanup_path, seek=new_seek,
                                 duration=track.duration, thumbnail=track.thumbnail,
                                 uploader=track.uploader))
        s.skip_event.set()
        return {"ok": True, "position": new_seek}

    def web_set_volume(self, pct: int) -> dict:
        if not 0 <= pct <= 200:
            raise ValueError("Volume must be between 0 and 200.")
        self._web_voice_state().volume = pct / 100.0
        return {"volume": pct}

    def web_remove_queued(self, index: int) -> Optional[dict]:
        """Remove the 1-based queue position. Returns the removed track, or None
        if out of range."""
        s = self._web_voice_state()
        if not 1 <= index <= len(s.queue):
            return None
        items = list(s.queue)
        removed = items.pop(index - 1)
        s.queue.clear()
        s.queue.extend(items)
        return self._track_dict(removed)

    def web_move_queued(self, src: int, dst: int) -> bool:
        """Move 1-based queue position `src` to `dst`. False if out of range."""
        s = self._web_voice_state()
        n = len(s.queue)
        if not (1 <= src <= n and 1 <= dst <= n):
            return False
        items = list(s.queue)
        items.insert(dst - 1, items.pop(src - 1))
        s.queue.clear()
        s.queue.extend(items)
        return True

    # saved playlists ----------------------------------------------------------

    def web_list_playlists(self) -> list[dict]:
        return [{"name": n, "count": len(t)} for n, t in sorted(self._saved_playlists.items())]

    def web_get_playlist(self, name: str) -> Optional[dict]:
        entries = self._saved_playlists.get(name)
        if entries is None:
            return None
        return {"name": name, "entries": [{"title": e["title"]} for e in entries]}

    def web_create_playlist(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise ValueError("Playlist name required.")
        if name in self._saved_playlists:
            raise ValueError(f"Playlist {name!r} already exists.")
        self._saved_playlists[name] = []
        self._save_playlists()

    def web_delete_playlist(self, name: str) -> bool:
        if name not in self._saved_playlists:
            return False
        del self._saved_playlists[name]
        self._save_playlists()
        return True

    async def web_add_to_playlist(self, name: str, query: str) -> Optional[dict]:
        if name not in self._saved_playlists:
            raise ValueError(f"No playlist named {name!r}.")
        entry = await self._resolve_pl_entry(query)
        if not entry:
            return None
        self._saved_playlists[name].append(entry)
        self._save_playlists()
        return {"title": entry["title"], "position": len(self._saved_playlists[name])}

    def web_remove_playlist_track(self, name: str, index: int) -> Optional[dict]:
        entries = self._saved_playlists.get(name)
        if entries is None:
            raise ValueError(f"No playlist named {name!r}.")
        if not 1 <= index <= len(entries):
            return None
        removed = entries.pop(index - 1)
        self._save_playlists()
        return {"title": removed["title"]}

    async def web_play_saved(self, name: str, shuffle: bool = False) -> dict:
        s = self._web_voice_state()
        entries = self._saved_playlists.get(name)
        if entries is None:
            raise ValueError(f"No playlist named {name!r}.")
        if not entries:
            raise ValueError(f"{name!r} is empty.")
        ordered = list(entries)
        if shuffle:
            random.shuffle(ordered)
        for e in ordered:
            s.queue.append(Track(title=e["title"], source=e["source"]))
        connected = await self._try_start_playback(self.voice_room_id)
        return {"queued": len(ordered), "connected": connected}

    async def web_import_playlist(self, url: str, name: str, limit: int) -> dict:
        """Create a NEW saved playlist from a playlist URL."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Playlist name required.")
        if name in self._saved_playlists:
            raise ValueError(f"Playlist {name!r} already exists.")
        tracks, err = await self._resolve_playlist(url, limit)
        if err:
            raise ValueError(err)
        self._saved_playlists[name] = [{"title": t.title, "source": t.source} for t in tracks]
        self._save_playlists()
        return {"name": name, "imported": len(tracks)}

    # ── main ──────────────────────────────────────────────────────────────────

    async def _login(self):
        if CREDENTIALS_PATH.exists():
            creds = json.loads(CREDENTIALS_PATH.read_text())
            self.matrix.restore_login(
                user_id=creds["user_id"],
                device_id=creds["device_id"],
                access_token=creds["access_token"],
            )
            log.info(f"Restored login as {self.matrix.user_id} (device {self.matrix.device_id})")
            return

        resp = await self.matrix.login(MATRIX_PASSWORD, device_name="music-bot")
        if not isinstance(resp, LoginResponse):
            raise RuntimeError(f"Login failed: {resp}")
        CREDENTIALS_PATH.write_text(json.dumps({
            "user_id": resp.user_id,
            "device_id": resp.device_id,
            "access_token": resp.access_token,
        }))
        log.info(f"Logged in as {resp.user_id} (device {resp.device_id}), saved credentials")

    async def run(self):
        await self._login()

        self.matrix.add_response_callback(self.on_sync, SyncResponse)
        self.matrix.add_event_callback(self.on_message, RoomMessageText)
        self.matrix.add_event_callback(self.on_audio, RoomMessageAudio)
        self.matrix.add_event_callback(self.on_file, RoomMessageFile)
        self.matrix.add_event_callback(self.on_megolm, MegolmEvent)

        web_runner = None
        if config.WEB_ENABLE:
            try:
                from web.server import start_web_server
                web_runner = await start_web_server(self)
            except Exception as e:
                log.error(f"Failed to start web server: {e}", exc_info=True)

        try:
            await self.matrix.sync_forever(timeout=30000, full_state=True)
        finally:
            if web_runner is not None:
                await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(MusicBot().run())
