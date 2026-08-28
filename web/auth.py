"""Minimal stateless auth for the web API.

A single shared password (WEB_PASSWORD) gates the browser. On login we issue an
HMAC-signed token (no external dep, no server-side session store) that the SPA
sends as a Bearer token. The signing secret is WEB_API_KEY (a strong random
value) when set, otherwise the password itself. Non-browser clients may still
use the X-API-Key header against WEB_API_KEY.
"""
import base64
import hashlib
import hmac
import json
import time

import config

_DEFAULT_TTL = 30 * 24 * 3600  # 30 days


def auth_enabled() -> bool:
    return bool(config.WEB_PASSWORD)


def _secret() -> bytes:
    return (config.WEB_API_KEY or config.WEB_PASSWORD or "").encode()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def make_token(ttl_seconds: int = _DEFAULT_TTL) -> str:
    body = _b64e(json.dumps({"exp": int(time.time()) + ttl_seconds}).encode())
    return f"{body}.{_sign(body)}"


def verify_token(token: str) -> bool:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(sig, _sign(body)):
        return False
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return False
    return float(payload.get("exp", 0)) > time.time()


def check_password(password: str) -> bool:
    expected = config.WEB_PASSWORD or ""
    return bool(expected) and hmac.compare_digest(password, expected)
