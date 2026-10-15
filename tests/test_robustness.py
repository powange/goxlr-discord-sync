import asyncio
import os

import pytest

from goxlr_discord_sync import config, paths, updates
from goxlr_discord_sync.app import App


def flaky_replace(monkeypatch, failures):
    """os.replace fails like on Windows while another process holds the file."""
    real = os.replace
    calls = {"n": 0}

    def _replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise PermissionError(13, "Access is denied", str(dst))
        return real(src, dst)

    monkeypatch.setattr(paths.os, "replace", _replace)
    return calls


def test_atomic_write_retries_when_file_is_busy(monkeypatch):
    monkeypatch.setattr(paths.time, "sleep", lambda s: None)
    calls = flaky_replace(monkeypatch, failures=3)
    config.save(config.Config(client_id="1"))
    assert config.load().client_id == "1"
    assert calls["n"] == 4


def test_atomic_write_gives_up_and_cleans_up(monkeypatch):
    monkeypatch.setattr(paths.time, "sleep", lambda s: None)
    flaky_replace(monkeypatch, failures=100)
    with pytest.raises(PermissionError):
        config.save(config.Config(client_id="1"))
    assert not list(paths.data_dir().glob("*.tmp"))


async def test_update_check_errors_do_not_escape(monkeypatch):
    """Background loops run in the app TaskGroup: an escaping error would stop the app."""
    app = App(config.Config())
    monkeypatch.setattr(app, "_notify", lambda *a: None)
    monkeypatch.setattr(updates, "fetch_latest_release", lambda: updates.Release("0.0.1", "https://p"))

    def broken(state):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(config, "save_state", broken)
    await app.check_updates()  # must log, not raise
    await app.check_updates(manual=True)


async def test_manual_check_waits_for_running_check(monkeypatch):
    app = App(config.Config())
    notes = []
    monkeypatch.setattr(app, "_notify", lambda title, msg: notes.append(title))
    gate = asyncio.Event()

    async def slow_fetch():
        await gate.wait()
        return updates.Release("0.0.1", "https://p")

    monkeypatch.setattr(app, "_fetch_release", slow_fetch)
    auto = asyncio.create_task(app.check_updates())
    await asyncio.sleep(0)
    manual = asyncio.create_task(app.check_updates(manual=True))
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(auto, manual)
    assert notes == ["No update available"]  # the click was not silently dropped
