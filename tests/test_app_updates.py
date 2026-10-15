import pytest

from goxlr_discord_sync import __version__, config, paths, updates
from goxlr_discord_sync.app import App

NEWER = updates.Release("99.0.0", "https://page", "notes", "https://dl/setup", "https://dl/sums")
SAME = updates.Release(__version__, "https://page")


@pytest.fixture
def app(monkeypatch):
    app = App(config.Config())
    app.notes = []
    app.installs = 0
    monkeypatch.setattr(app, "_notify", lambda title, message: app.notes.append(title))

    def fake_install():
        app.installs += 1

    monkeypatch.setattr(app, "install_update", fake_install)
    return app


def frozen(monkeypatch, value=True):
    monkeypatch.setattr(paths, "IS_FROZEN", value)
    monkeypatch.setattr(paths, "IS_WINDOWS", value)


async def test_manual_check_up_to_date(app, monkeypatch):
    monkeypatch.setattr(updates, "fetch_latest_release", lambda: SAME)
    app.update_info = NEWER  # stale info from a previous run
    await app.check_updates(manual=True)
    assert app.notes == ["No update available"]
    assert app.update_info is None
    assert config.load_state()["last_update_check"] > 0


async def test_manual_check_offers_installation(app, monkeypatch):
    frozen(monkeypatch)
    monkeypatch.setattr(updates, "fetch_latest_release", lambda: NEWER)
    await app.check_updates(manual=True)
    assert app.installs == 1  # confirmation dialog, no extra notification
    assert app.notes == []


async def test_manual_check_from_source_notifies(app, monkeypatch):
    frozen(monkeypatch, False)
    monkeypatch.setattr(updates, "fetch_latest_release", lambda: NEWER)
    await app.check_updates(manual=True)
    await app.check_updates(manual=True)
    assert app.installs == 0
    assert app.notes == ["Update available", "Update available"]


async def test_manual_check_failure(app, monkeypatch):
    monkeypatch.setattr(updates, "fetch_latest_release", lambda: None)
    await app.check_updates(manual=True)
    assert app.notes == ["Update check failed"]


async def test_automatic_check_notifies_each_version_once(app, monkeypatch):
    frozen(monkeypatch)
    monkeypatch.setattr(updates, "fetch_latest_release", lambda: NEWER)
    await app.check_updates()
    await app.check_updates()
    assert app.notes == ["Update available"]
    assert app.installs == 0  # never installs without the user asking
    assert app.update_info == NEWER
