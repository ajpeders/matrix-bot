"""Test setup: stub out nio/livekit so tests can run without libolm/livekit installed."""
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# bot.py reads required env vars at import time — set them before importing.
os.environ.setdefault("MATRIX_HOMESERVER", "https://test.example.com")
os.environ.setdefault("MATRIX_USER", "@bot:test.example.com")
os.environ.setdefault("MATRIX_PASSWORD", "test-password")
os.environ.setdefault("LIVEKIT_URL", "wss://lk.test.example.com")
os.environ.setdefault("LIVEKIT_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-secret")

if "nio" not in sys.modules:
    try:
        import nio  # noqa: F401
        import nio.crypto  # noqa: F401
    except ImportError:
        nio_stub = types.ModuleType("nio")
        nio_stub.AsyncClient = MagicMock
        nio_stub.AsyncClientConfig = MagicMock
        nio_stub.LoginResponse = type("LoginResponse", (), {})
        nio_stub.MatrixRoom = type("MatrixRoom", (), {})
        nio_stub.MegolmEvent = type("MegolmEvent", (), {})
        nio_stub.RoomMessageAudio = type("RoomMessageAudio", (), {})
        nio_stub.RoomMessageFile = type("RoomMessageFile", (), {})
        nio_stub.RoomMessageText = type("RoomMessageText", (), {})
        nio_stub.SyncResponse = type("SyncResponse", (), {})
        sys.modules["nio"] = nio_stub
        crypto_stub = types.ModuleType("nio.crypto")
        crypto_stub.decrypt_attachment = MagicMock(return_value=b"")
        sys.modules["nio.crypto"] = crypto_stub

if "livekit" not in sys.modules:
    try:
        import livekit  # noqa: F401
    except ImportError:
        lk = types.ModuleType("livekit")
        lk.api = MagicMock()
        lk.rtc = MagicMock()
        sys.modules["livekit"] = lk
        sys.modules["livekit.api"] = lk.api
        sys.modules["livekit.rtc"] = lk.rtc

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
import bot as bot_module  # noqa: E402,F401  (re-exported for tests via fixture)


@pytest.fixture
def make_bot(tmp_path, monkeypatch):
    """Construct a MusicBot with a mocked Matrix client and tmp state dir."""
    monkeypatch.setattr(bot_module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bot_module, "CRYPTO_STORE_DIR", tmp_path / "store")
    monkeypatch.setattr(bot_module, "CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr(bot_module, "PLAYLISTS_PATH", tmp_path / "playlists.json")

    def factory(voice_room_id=None):
        if voice_room_id is None:
            monkeypatch.delenv("VOICE_ROOM_ID", raising=False)
        else:
            monkeypatch.setenv("VOICE_ROOM_ID", voice_room_id)

        fake_client = AsyncMock()
        fake_client.user_id = "@bot:test.example.com"
        fake_client.device_id = "TESTDEV"
        fake_client.restore_login = MagicMock()  # sync method on real AsyncClient
        # `.rooms` is a real dict on AsyncClient, not awaitable — keep it that way
        # so send()'s encrypted-room precheck doesn't get a coroutine back.
        fake_client.rooms = {}
        fake_client.should_share_group_session = MagicMock(return_value=False)

        monkeypatch.setattr(bot_module, "AsyncClient", lambda *a, **kw: fake_client)
        return bot_module.MusicBot()

    return factory
