"""Windows-only checks (DPAPI, registry, Discord named pipe). Read-only on the real system."""

import asyncio
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_secrets_are_dpapi_encrypted():
    from goxlr_discord_sync import paths, secure_store

    secure_store.set_client_secret("very-secret-value")
    raw = paths.secrets_file().read_bytes()
    assert raw.startswith(b"DPAPI1")
    assert b"very-secret-value" not in raw
    assert secure_store.get_client_secret() == "very-secret-value"


def test_autostart_registry_is_readable():
    from goxlr_discord_sync import autostart

    autostart.registered_command()  # must not raise
    autostart.is_enabled()


async def test_discord_named_pipe_transport():
    """Opens Discord's pipe when Discord runs; a bogus client id is refused without any UI."""
    from goxlr_discord_sync.discord_ipc import DiscordIPC, DiscordUnavailable

    ipc = DiscordIPC("1")
    try:
        await ipc.connect(timeout=5)
    except DiscordUnavailable as e:
        if "not running" in str(e):
            pytest.skip("Discord is not running")
        assert "Client ID" in str(e) or "handshake" in str(e)
    finally:
        await ipc.close()
    await asyncio.sleep(0)
