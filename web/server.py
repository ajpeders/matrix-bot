"""In-process HTTP API for the Matrix music bot.

Runs inside the bot's asyncio event loop (started from MusicBot.run), so it can
read and control the live RoomState directly — no IPC, no locking (single
asyncio thread). The React SPA (separate nginx container) is the only intended
client; Traefik routes /api/* here and everything else to the SPA.

The bot is single-voice-room (VOICE_ROOM_ID), so unlike the Discord sibling
there is no per-guild dimension — every route acts on the one voice room.

All bot.web_* methods raise ValueError(message) for user-fixable problems; the
error middleware maps those to HTTP 422.
"""
import logging

from aiohttp import web

import config
from web import auth

logger = logging.getLogger(__name__)

BOT_KEY = web.AppKey("bot", object)


# --- helpers ---------------------------------------------------------------

def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _body(request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _int_param(request, name: str) -> int:
    try:
        return int(request.match_info[name])
    except (KeyError, ValueError):
        raise web.HTTPBadRequest(reason=f"Invalid {name}.")


# --- middleware ------------------------------------------------------------

_AUTH_EXEMPT = {"/api/health", "/api/login"}


@web.middleware
async def _auth_middleware(request, handler):
    if request.path in _AUTH_EXEMPT:
        return await handler(request)
    # Non-browser clients: X-API-Key against WEB_API_KEY.
    if config.WEB_API_KEY and request.headers.get("X-API-Key") == config.WEB_API_KEY:
        return await handler(request)
    # Browser: Bearer token issued by /api/login (when a password is configured).
    if auth.auth_enabled():
        authz = request.headers.get("Authorization", "")
        token = authz[7:] if authz.startswith("Bearer ") else ""
        if not auth.verify_token(token):
            return _json_error(401, "Authentication required.")
        return await handler(request)
    # No password set: if an API key is required but absent, reject; else open (LAN).
    if config.WEB_API_KEY:
        return _json_error(401, "Invalid or missing API key.")
    return await handler(request)


@web.middleware
async def _error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except ValueError as exc:
        # User-fixable problem surfaced by a bot.web_* method.
        return _json_error(422, str(exc))
    except Exception as exc:  # noqa: BLE001 — surface as JSON, log details
        logger.exception("web_handler_error path=%s: %s", request.path, exc)
        return _json_error(500, "Internal error handling request.")


# --- handlers --------------------------------------------------------------

async def health(request):
    return web.json_response({"ok": True})


async def login(request):
    if not auth.auth_enabled():
        return _json_error(400, "Login is not enabled on this server.")
    body = await _body(request)
    if not auth.check_password(body.get("password") or ""):
        return _json_error(401, "Incorrect password.")
    return web.json_response({"token": auth.make_token()})


async def auth_config(request):
    """Lets the SPA know whether to show a login screen before any token exists."""
    return web.json_response({"auth_required": auth.auth_enabled()})


async def status(request):
    bot = request.app[BOT_KEY]
    np = bot.web_now_playing()
    return web.json_response({
        "bot": str(bot.matrix.user_id) if bot.matrix.user_id else None,
        "voice_room": bot.voice_room_id,
        "configured": np["configured"],
        "connected": np["connected"],
        "now_playing": np["current"]["title"] if np["current"] else None,
        "queue_length": len(np["queue"]),
        "paused": np["paused"],
    })


async def now_playing(request):
    bot = request.app[BOT_KEY]
    return web.json_response(bot.web_now_playing())


async def search(request):
    bot = request.app[BOT_KEY]
    q = (request.query.get("q") or "").strip()
    if not q:
        raise web.HTTPBadRequest(reason="q required.")
    try:
        limit = int(request.query.get("limit", "5"))
    except ValueError:
        limit = 5
    limit = max(1, min(limit, 10))
    return web.json_response({"results": await bot.web_search(q, limit)})


async def queue_track(request):
    bot = request.app[BOT_KEY]
    body = await _body(request)
    query = (body.get("query") or "").strip()
    if not query:
        raise web.HTTPBadRequest(reason="query required.")
    return web.json_response(await bot.web_enqueue(query))


async def queue_playlist(request):
    bot = request.app[BOT_KEY]
    body = await _body(request)
    url = (body.get("url") or "").strip()
    if not url:
        raise web.HTTPBadRequest(reason="url required.")
    limit = _clamp_limit(body.get("limit"))
    return web.json_response(await bot.web_enqueue_playlist(url, limit))


async def remove_queue_track(request):
    bot = request.app[BOT_KEY]
    index = _int_param(request, "index")
    removed = bot.web_remove_queued(index)
    if removed is None:
        raise web.HTTPNotFound(reason="No track at that position.")
    return web.json_response({"removed": removed})


async def move_queue_track(request):
    bot = request.app[BOT_KEY]
    body = await _body(request)
    try:
        src = int(body["from"])
        dst = int(body["to"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(reason="'from' and 'to' are required integers.")
    if not bot.web_move_queued(src, dst):
        raise web.HTTPBadRequest(reason="Indices out of range.")
    return web.json_response({"ok": True})


async def playback_control(request):
    bot = request.app[BOT_KEY]
    action = request.match_info["action"]
    return web.json_response(await bot.web_playback(action))


async def seek(request):
    bot = request.app[BOT_KEY]
    body = await _body(request)
    try:
        delta = float(body["delta"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(reason="'delta' (seconds, signed) required.")
    return web.json_response(await bot.web_seek(delta))


async def set_volume(request):
    bot = request.app[BOT_KEY]
    body = await _body(request)
    try:
        pct = int(body["volume"])
    except (KeyError, TypeError, ValueError):
        raise web.HTTPBadRequest(reason="'volume' (0-200) required.")
    return web.json_response(bot.web_set_volume(pct))


# --- saved playlist handlers -----------------------------------------------

def _clamp_limit(raw, default=50) -> int:
    try:
        return max(1, min(int(raw), 200))
    except (TypeError, ValueError):
        return default


async def list_playlists(request):
    bot = request.app[BOT_KEY]
    return web.json_response({"playlists": bot.web_list_playlists()})


async def get_playlist(request):
    bot = request.app[BOT_KEY]
    pl = bot.web_get_playlist(request.match_info["name"])
    if pl is None:
        raise web.HTTPNotFound(reason="No such playlist.")
    return web.json_response(pl)


async def create_playlist(request):
    bot = request.app[BOT_KEY]
    body = await _body(request)
    bot.web_create_playlist(body.get("name") or "")
    return web.json_response({"ok": True})


async def import_playlist(request):
    bot = request.app[BOT_KEY]
    body = await _body(request)
    url = (body.get("url") or "").strip()
    if not url:
        raise web.HTTPBadRequest(reason="url required.")
    limit = _clamp_limit(body.get("limit"))
    return web.json_response(await bot.web_import_playlist(url, body.get("name") or "", limit))


async def add_to_playlist(request):
    bot = request.app[BOT_KEY]
    name = request.match_info["name"]
    body = await _body(request)
    query = (body.get("query") or "").strip()
    if not query:
        raise web.HTTPBadRequest(reason="query required.")
    added = await bot.web_add_to_playlist(name, query)
    if added is None:
        return _json_error(422, "Couldn't resolve that track.")
    return web.json_response(added)


async def delete_playlist(request):
    bot = request.app[BOT_KEY]
    if not bot.web_delete_playlist(request.match_info["name"]):
        raise web.HTTPNotFound(reason="No such playlist.")
    return web.json_response({"ok": True})


async def remove_playlist_track(request):
    bot = request.app[BOT_KEY]
    name = request.match_info["name"]
    index = _int_param(request, "index")
    removed = bot.web_remove_playlist_track(name, index)
    if removed is None:
        raise web.HTTPNotFound(reason="No track at that position.")
    return web.json_response({"removed": removed})


async def play_playlist(request):
    bot = request.app[BOT_KEY]
    name = request.match_info["name"]
    body = await _body(request)
    shuffle = bool(body.get("shuffle", False))
    return web.json_response(await bot.web_play_saved(name, shuffle))


# --- app factory -----------------------------------------------------------

def create_app(bot) -> web.Application:
    app = web.Application(middlewares=[_error_middleware, _auth_middleware])
    app[BOT_KEY] = bot
    app.add_routes([
        web.get("/api/health", health),
        web.post("/api/login", login),
        web.get("/api/auth-config", auth_config),
        web.get("/api/status", status),
        web.get("/api/now-playing", now_playing),
        web.get("/api/search", search),
        web.post("/api/queue", queue_track),
        web.post("/api/queue/playlist", queue_playlist),
        web.post("/api/queue/move", move_queue_track),
        web.delete("/api/queue/{index}", remove_queue_track),
        web.post("/api/playback/{action}", playback_control),
        web.post("/api/seek", seek),
        web.post("/api/volume", set_volume),
        web.get("/api/playlists", list_playlists),
        web.post("/api/playlists", create_playlist),
        web.post("/api/playlists/import", import_playlist),
        web.get("/api/playlists/{name}", get_playlist),
        web.post("/api/playlists/{name}", add_to_playlist),
        web.delete("/api/playlists/{name}", delete_playlist),
        web.delete("/api/playlists/{name}/tracks/{index}", remove_playlist_track),
        web.post("/api/playlists/{name}/play", play_playlist),
    ])
    return app


async def start_web_server(bot) -> web.AppRunner:
    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.WEB_PORT)
    await site.start()
    logger.info("Web API listening on 0.0.0.0:%d", config.WEB_PORT)
    return runner
