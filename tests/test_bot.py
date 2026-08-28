"""Tests for the matrix music bot."""
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot as bot_module


# ── helpers ────────────────────────────────────────────────────────────────

class _Room:
    def __init__(self, room_id):
        self.room_id = room_id


def _text(body, sender="@user:test"):
    e = MagicMock()
    e.body = body
    e.sender = sender
    return e


def _file_event(sender="@user:test", body="track.mp3", url="mxc://test/abc"):
    e = MagicMock()
    e.sender = sender
    e.body = body
    e.url = url
    return e


# ── command -> voice-room queue routing (regression for the half-finished split) ─

async def test_play_url_queues_into_voice_room(make_bot):
    b = make_bot(voice_room_id="!voice:test")
    track = bot_module.Track(title="T", source="yt://https://yt/x")
    b._yt_resolve = AsyncMock(return_value=track)
    b.ensure_playback = AsyncMock()

    await b.on_message(_Room("!cmd:test"), _text("!play https://yt/x"))

    assert list(b.state("!voice:test").queue) == [track]
    # command room should NOT have received the queued track
    assert "!cmd:test" not in b.rooms or len(b.state("!cmd:test").queue) == 0


async def test_play_url_no_voice_room_uses_command_room(make_bot):
    b = make_bot(voice_room_id=None)
    track = bot_module.Track(title="T", source="yt://https://yt/x")
    b._yt_resolve = AsyncMock(return_value=track)
    b.ensure_playback = AsyncMock()

    await b.on_message(_Room("!cmd:test"), _text("!play https://yt/x"))

    assert list(b.state("!cmd:test").queue) == [track]


async def test_audio_file_upload_queues_into_voice_room(make_bot):
    b = make_bot(voice_room_id="!voice:test")
    b._download_attachment = AsyncMock(return_value="/tmp/file.mp3")
    b.ensure_playback = AsyncMock()

    await b._queue_audio_file("!cmd:test", _file_event(body="song.mp3"), "song.mp3")

    voice_q = b.state("!voice:test").queue
    assert len(voice_q) == 1
    assert voice_q[0].title == "song.mp3"
    assert voice_q[0].source == "/tmp/file.mp3"
    assert "!cmd:test" not in b.rooms or len(b.state("!cmd:test").queue) == 0


# ── transport commands act on voice-room state ─────────────────────────────

async def test_skip_sets_event_on_voice_room(make_bot):
    b = make_bot(voice_room_id="!voice:test")
    await b.on_message(_Room("!cmd:test"), _text("!skip"))
    assert b.state("!voice:test").skip_event.is_set()


async def test_stop_clears_voice_room_queue(make_bot):
    b = make_bot(voice_room_id="!voice:test")
    b.state("!voice:test").queue.append(bot_module.Track(title="X", source="/x"))

    await b.on_message(_Room("!cmd:test"), _text("!stop"))

    s = b.state("!voice:test")
    assert s.stop_event.is_set()
    assert s.skip_event.is_set()
    assert len(s.queue) == 0


async def test_pause_resume_toggle(make_bot):
    b = make_bot(voice_room_id="!voice:test")
    await b.on_message(_Room("!cmd:test"), _text("!pause"))
    assert b.state("!voice:test").paused is True
    await b.on_message(_Room("!cmd:test"), _text("!resume"))
    assert b.state("!voice:test").paused is False


# ── volume parsing ────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ("!volume 0", 0.0),
    ("!volume 50", 0.5),
    ("!volume 100", 1.0),
    ("!volume 200", 2.0),
])
async def test_volume_valid(make_bot, body, expected):
    b = make_bot()
    await b.on_message(_Room("!cmd:test"), _text(body))
    assert b.state("!cmd:test").volume == pytest.approx(expected)


@pytest.mark.parametrize("body", ["!volume 201", "!volume -1", "!volume abc"])
async def test_volume_invalid_keeps_default(make_bot, body):
    b = make_bot()
    initial = b.state("!cmd:test").volume
    await b.on_message(_Room("!cmd:test"), _text(body))
    assert b.state("!cmd:test").volume == initial


# ── queue display ─────────────────────────────────────────────────────────

async def test_queue_empty_message(make_bot):
    b = make_bot()
    b.send = AsyncMock()
    await b.on_message(_Room("!cmd:test"), _text("!queue"))
    b.send.assert_called_once_with("!cmd:test", "The queue is empty.")


async def test_queue_lists_current_then_pending(make_bot):
    b = make_bot()
    b.send = AsyncMock()
    s = b.state("!cmd:test")
    s.current = bot_module.Track(title="Now", source="/now")
    s.queue.append(bot_module.Track(title="Next1", source="/n1"))
    s.queue.append(bot_module.Track(title="Next2", source="/n2"))

    await b.on_message(_Room("!cmd:test"), _text("!queue"))

    text = b.send.call_args.args[1]
    assert "Now" in text
    assert "1. Next1" in text
    assert "2. Next2" in text


# ── file/audio handlers ───────────────────────────────────────────────────

async def test_on_file_ignores_non_audio_extension(make_bot):
    b = make_bot()
    pending = ("!cmd:test", time.monotonic() + 60)
    b._pending_upload["@user:test"] = pending
    b._queue_audio_file = AsyncMock()

    await b.on_file(_Room("!cmd:test"), _file_event(body="report.pdf"))

    b._queue_audio_file.assert_not_called()
    assert b._pending_upload["@user:test"] == pending  # not popped


async def test_on_file_queues_audio_extension(make_bot):
    b = make_bot()
    b._pending_upload["@user:test"] = ("!cmd:test", time.monotonic() + 60)
    b._queue_audio_file = AsyncMock()

    event = _file_event(body="track.flac")
    await b.on_file(_Room("!cmd:test"), event)

    b._queue_audio_file.assert_called_once_with("!cmd:test", event, "track.flac")
    assert "@user:test" not in b._pending_upload


async def test_on_audio_queues_when_pending(make_bot):
    b = make_bot()
    b._pending_upload["@user:test"] = ("!cmd:test", time.monotonic() + 60)
    b._queue_audio_file = AsyncMock()

    await b.on_audio(_Room("!cmd:test"), _file_event(body="song.mp3"))

    b._queue_audio_file.assert_called_once()


async def test_on_audio_ignores_when_not_pending(make_bot):
    b = make_bot()
    b._queue_audio_file = AsyncMock()

    await b.on_audio(_Room("!cmd:test"), _file_event(body="song.mp3"))

    b._queue_audio_file.assert_not_called()


async def test_pending_upload_expires_after_ttl(make_bot):
    b = make_bot()
    # entry that "expired" 1 second ago
    b._pending_upload["@user:test"] = ("!cmd:test", time.monotonic() - 1)
    b._queue_audio_file = AsyncMock()

    await b.on_audio(_Room("!cmd:test"), _file_event(body="song.mp3"))

    b._queue_audio_file.assert_not_called()
    # expired entry should have been popped on inspection
    assert "@user:test" not in b._pending_upload


async def test_pending_upload_rejects_wrong_room(make_bot):
    b = make_bot()
    b._pending_upload["@user:test"] = ("!other:test", time.monotonic() + 60)
    b._queue_audio_file = AsyncMock()

    await b.on_audio(_Room("!cmd:test"), _file_event(body="song.mp3"))

    b._queue_audio_file.assert_not_called()


async def test_play_command_records_pending_with_ttl(make_bot):
    b = make_bot()

    before = time.monotonic()
    await b.on_message(_Room("!cmd:test"), _text("!play"))
    after = time.monotonic()

    assert "@user:test" in b._pending_upload
    target_room, expires_at = b._pending_upload["@user:test"]
    assert target_room == "!cmd:test"
    assert before + bot_module.PENDING_UPLOAD_TTL <= expires_at <= after + bot_module.PENDING_UPLOAD_TTL


async def test_audio_file_track_records_cleanup_path(make_bot):
    b = make_bot(voice_room_id="!voice:test")
    b._download_attachment = AsyncMock(return_value="/tmp/file.mp3")
    b.ensure_playback = AsyncMock()

    await b._queue_audio_file("!cmd:test", _file_event(body="song.mp3"), "song.mp3")

    track = b.state("!voice:test").queue[0]
    assert track.cleanup_path == "/tmp/file.mp3"


async def test_youtube_track_has_no_cleanup_path(make_bot):
    b = make_bot()
    track = bot_module.Track(title="T", source="yt://https://yt/x")
    b._yt_resolve = AsyncMock(return_value=track)
    b.ensure_playback = AsyncMock()

    await b.on_message(_Room("!cmd:test"), _text("!play https://yt/x"))

    queued = b.state("!cmd:test").queue[0]
    assert queued.cleanup_path is None


# ── yt-dlp playlist param stripping ───────────────────────────────────────

def test_strip_playlist_params_keeps_video_drops_playlist():
    url = "https://youtube.com/watch?v=abc&list=PL123&index=4&start_radio=1&playlist=foo"
    stripped = bot_module.strip_playlist_params(url)
    assert "v=abc" in stripped
    assert "list=" not in stripped
    assert "index=" not in stripped
    assert "start_radio=" not in stripped
    assert "playlist=" not in stripped


def test_playlist_url_detects_apple_albums_and_soundcloud_sets():
    assert bot_module.is_playlist_url(
        "https://music.apple.com/us/album/take-care/1440642493"
    )
    assert bot_module.is_playlist_url(
        "https://music.apple.com/us/playlist/foo/pl.abc123"
    )
    assert not bot_module.is_playlist_url(
        "https://music.apple.com/us/album/take-care/1440642493?i=1440642618"
    )
    assert bot_module.is_playlist_url(
        "https://soundcloud.com/artist/sets/album-name"
    )


# ── login flow ────────────────────────────────────────────────────────────

async def test_login_restores_from_credentials(make_bot, tmp_path):
    creds = {"user_id": "@bot:test", "device_id": "DEVABC", "access_token": "tok"}
    (tmp_path / "credentials.json").write_text(json.dumps(creds))

    b = make_bot()
    b.matrix.login = AsyncMock()

    await b._login()

    b.matrix.restore_login.assert_called_once_with(
        user_id="@bot:test", device_id="DEVABC", access_token="tok",
    )
    b.matrix.login.assert_not_called()


async def test_login_fresh_saves_credentials(make_bot, tmp_path):
    b = make_bot()
    fake_resp = MagicMock(spec=bot_module.LoginResponse)
    fake_resp.user_id = "@bot:test"
    fake_resp.device_id = "NEWDEV"
    fake_resp.access_token = "newtok"
    b.matrix.login = AsyncMock(return_value=fake_resp)

    await b._login()

    b.matrix.login.assert_awaited_once()
    b.matrix.restore_login.assert_not_called()
    saved = json.loads((tmp_path / "credentials.json").read_text())
    assert saved == {"user_id": "@bot:test", "device_id": "NEWDEV", "access_token": "newtok"}


async def test_login_raises_on_unexpected_response(make_bot):
    b = make_bot()
    b.matrix.login = AsyncMock(return_value=MagicMock())  # not a LoginResponse instance

    with pytest.raises(RuntimeError, match="Login failed"):
        await b._login()
