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


def test_apps_list_entry(monkeypatch, tmp_path):
    import winreg

    from goxlr_discord_sync import installer

    exe = tmp_path / "GoXLR_Discord_Sync.exe"
    exe.write_bytes(b"x" * 4096)
    installer.register_uninstall(exe, "9.9.9")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, installer.UNINSTALL_KEY) as key:
        assert winreg.QueryValueEx(key, "DisplayVersion")[0] == "9.9.9"
        assert winreg.QueryValueEx(key, "UninstallString")[0].endswith("--uninstall")
        assert winreg.QueryValueEx(key, "EstimatedSize")[0] == 4
    installer.unregister_uninstall()
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, installer.UNINSTALL_KEY)


def test_self_delete_removes_file_and_empty_folder(tmp_path):
    import time

    from goxlr_discord_sync import installer

    folder = tmp_path / "it's here"  # quote in the path
    folder.mkdir()
    exe = folder / "GoXLR_Discord_Sync.exe"
    exe.write_bytes(b"x")
    installer.schedule_self_delete(exe)
    for _ in range(40):
        if not folder.exists():
            break
        time.sleep(0.25)
    assert not folder.exists()


def test_registry_autostart_uses_the_test_entry():
    from goxlr_discord_sync import autostart

    assert "TEST" in autostart.VALUE_NAME  # conftest.isolated_system
    autostart.enable([r"C:\Program Files\Test App\app.exe", "--flag"])
    assert autostart.registered_command() == '"C:\\Program Files\\Test App\\app.exe" --flag'
    assert autostart.is_enabled()
    autostart.disable()
    assert not autostart.is_enabled()
