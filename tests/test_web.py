"""Tests for the in-process web API (web/server.py)."""
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

import bot as bot_module
from web.server import create_app


@pytest.fixture
async def client(make_bot):
    """An aiohttp test client wired to a stubbed bot with a voice room set."""
    b = make_bot(voice_room_id="!voice:test")
    b.ensure_playback = AsyncMock()
    c = TestClient(TestServer(create_app(b)))
    await c.start_server()
    c.bot = b  # expose for assertions
    yield c
    await c.close()


async def test_health_is_open(client):
    resp = await client.get("/api/health")
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True}


async def test_now_playing_empty_shape(client):
    resp = await client.get("/api/now-playing")
    body = await resp.json()
    assert body["configured"] is True
    assert body["current"] is None
    assert body["queue"] == []
    assert body["volume"] == 100


async def test_now_playing_reports_current_and_queue(client):
    s = client.bot.state("!voice:test")
    s.current = bot_module.Track(title="Now", source="yt://https://yt/x")
    s.queue.append(bot_module.Track(title="Next", source="/tmp/song.mp3"))
    resp = await client.get("/api/now-playing")
    body = await resp.json()
    assert body["current"] == {
        "title": "Now",
        "url": "https://yt/x",
        "source": "youtube",
        "duration": None,
        "uploader": None,
        "thumbnail": None,
        "requester": None,
    }
    assert body["queue"] == [{
        "title": "Next",
        "url": None,
        "source": "local",
        "duration": None,
        "uploader": None,
        "thumbnail": None,
        "requester": None,
    }]


async def test_queue_track_resolves_and_enqueues(client):
    track = bot_module.Track(title="Resolved", source="yt://https://yt/x")
    client.bot._yt_resolve = AsyncMock(return_value=track)
    resp = await client.post("/api/queue", json={"query": "some song"})
    assert resp.status == 200
    body = await resp.json()
    assert body["queued"] == 1
    assert list(client.bot.state("!voice:test").queue) == [track]


async def test_queue_apple_music_track_resolves_via_search_pair(client):
    track = bot_module.Track(title="Artist — Song", source="yt://ytsearch1:Artist Song")
    client.bot._apple_music_resolve = AsyncMock(return_value=([("Song", "Artist")], None))
    client.bot._tracks_from_search_pairs = AsyncMock(return_value=[track])
    resp = await client.post(
        "/api/queue",
        json={"query": "https://music.apple.com/us/song/song/123"},
    )
    assert resp.status == 200
    assert list(client.bot.state("!voice:test").queue) == [track]


async def test_queue_track_requires_query(client):
    resp = await client.post("/api/queue", json={})
    assert resp.status == 400


async def test_pause_resume_actions(client):
    s = client.bot.state("!voice:test")
    assert (await client.post("/api/playback/pause")).status == 200
    assert s.paused is True
    await client.post("/api/playback/resume")
    assert s.paused is False


async def test_unknown_playback_action_is_422(client):
    resp = await client.post("/api/playback/frobnicate")
    assert resp.status == 422


async def test_volume_sets_and_validates(client):
    resp = await client.post("/api/volume", json={"volume": 50})
    assert resp.status == 200
    assert client.bot.state("!voice:test").volume == 0.5
    assert (await client.post("/api/volume", json={"volume": 999})).status == 422


async def test_remove_queue_index(client):
    s = client.bot.state("!voice:test")
    s.queue.append(bot_module.Track(title="A", source="/a.mp3"))
    s.queue.append(bot_module.Track(title="B", source="/b.mp3"))
    resp = await client.delete("/api/queue/1")
    assert resp.status == 200
    assert [t.title for t in s.queue] == ["B"]
    assert (await client.delete("/api/queue/9")).status == 404


async def test_seek_requires_current(client):
    # Nothing playing -> 422 from web_seek
    assert (await client.post("/api/seek", json={"delta": -15})).status == 422
    client.bot.state("!voice:test").current = bot_module.Track(title="X", source="yt://u")
    resp = await client.post("/api/seek", json={"delta": -15})
    assert resp.status == 200


async def test_saved_playlist_crud(client):
    # create
    assert (await client.post("/api/playlists", json={"name": "road"})).status == 200
    # duplicate create -> 422
    assert (await client.post("/api/playlists", json={"name": "road"})).status == 422
    # list
    body = await (await client.get("/api/playlists")).json()
    assert body["playlists"] == [{"name": "road", "count": 0}]
    # add a track (resolution mocked)
    client.bot._resolve_pl_entry = AsyncMock(return_value={"title": "T", "source": "yt://u"})
    resp = await client.post("/api/playlists/road", json={"query": "t"})
    assert resp.status == 200
    # get
    got = await (await client.get("/api/playlists/road")).json()
    assert got == {"name": "road", "entries": [{"title": "T"}]}
    # remove track
    assert (await client.delete("/api/playlists/road/tracks/1")).status == 200
    # delete playlist
    assert (await client.delete("/api/playlists/road")).status == 200
    assert (await client.get("/api/playlists/road")).status == 404


async def test_play_saved_playlist_enqueues(client):
    client.bot._saved_playlists["mix"] = [
        {"title": "One", "source": "yt://1"},
        {"title": "Two", "source": "yt://2"},
    ]
    resp = await client.post("/api/playlists/mix/play", json={})
    assert resp.status == 200
    assert (await resp.json())["queued"] == 2
    assert [t.title for t in client.bot.state("!voice:test").queue] == ["One", "Two"]
