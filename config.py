"""Web-API configuration, read from the environment.

Kept separate from bot.py so the `web/` package can import settings without
importing bot.py (which is the `__main__` entrypoint — importing it from a
submodule would re-execute it as a second module). bot.py reads these too.
"""
import os

# Port the in-process aiohttp API listens on (0.0.0.0). The React SPA (separate
# nginx container) is the only intended client; Traefik routes /api/* here.
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))

# Set WEB_ENABLE=0 to run the bot with no web API at all.
WEB_ENABLE = os.environ.get("WEB_ENABLE", "1") not in ("0", "false", "False", "")

# Shared password that gates the browser. Empty = no login required (LAN-only
# deployments behind Traefik's local-only middleware).
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

# Strong random value used both as the token-signing secret and as an X-API-Key
# for non-browser clients. Falls back to the password for signing when unset.
WEB_API_KEY = os.environ.get("WEB_API_KEY", "")
