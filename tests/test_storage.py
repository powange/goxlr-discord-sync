import json
import sys

import pytest

from goxlr_discord_sync import autostart, config, migration, paths, secure_store, updates
from goxlr_discord_sync.icons import render, save_ico


def test_config_roundtrip_and_validation():
    cfg = config.Config(client_id="42", mute_triggers=["All"], oauth_port=9000)
    config.save(cfg)
    assert config.load() == cfg
    loaded = config.Config.from_dict({
        "client_id": " 7 ", "cough_sync": "yes", "oauth_port": 99999, "mute_triggers": ["All", "Bogus"],
        "goxlr_url": "", "unknown": 1,
    })
    assert loaded.client_id == "7"
    assert loaded.cough_sync is True  # invalid type ignored
    assert loaded.oauth_port == config.DEFAULT_OAUTH_PORT
    assert loaded.mute_triggers == ["All"]
    assert loaded.goxlr_url == config.DEFAULT_GOXLR_URL
    assert loaded.redirect_uri == "http://127.0.0.1:9543/callback"


def test_corrupted_config_falls_back_to_defaults():
    paths.config_file().write_text("{not json", encoding="utf-8")
    assert config.load() == config.Config()


def test_secure_store_roundtrip():
    secure_store.set_client_secret("abc")
    secure_store.set_token({"access_token": "t"})
    assert secure_store.get_client_secret() == "abc"
    assert secure_store.get_token() == {"access_token": "t"}
    assert b"abc" not in paths.config_file().read_bytes() if paths.config_file().exists() else True
    secure_store.set_client_secret("other")  # new secret: old token is useless
    assert secure_store.get_token() is None
    if sys.platform != "win32":
        assert (paths.secrets_file().stat().st_mode & 0o777) == 0o600


def test_unreadable_secrets_are_ignored():
    paths.secrets_file().write_bytes(b"garbage")
    assert secure_store.load() == {}


def test_migrates_version_1_files(tmp_path):
    legacy = tmp_path / "install"
    legacy.mkdir()
    (legacy / "client_id.txt").write_text("123456\n")
    (legacy / "client_secret.txt").write_text("sec\n")
    (legacy / "discord_token.json").write_text(json.dumps({"access_token": "a", "refresh_token": "r"}))
    assert migration.migrate_legacy_files(legacy)
    assert config.load().client_id == "123456"
    assert secure_store.get_client_secret() == "sec"
    assert secure_store.get_token()["refresh_token"] == "r"
    assert not any((legacy / name).exists() for name in migration.LEGACY_FILES)
    assert not migration.migrate_legacy_files(legacy)


@pytest.mark.skipif(sys.platform == "win32", reason="would modify the real HKCU Run key")
def test_linux_autostart(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert not autostart.is_enabled()
    autostart.enable(["/opt/app dir/run", "--flag"])
    assert autostart.is_enabled()
    assert autostart.registered_command() == "'/opt/app dir/run' --flag"
    autostart.disable()
    assert not autostart.is_enabled()


def test_version_comparison():
    assert updates.is_newer("2.1.0", "2.0.0")
    assert updates.is_newer("v2.0.1", "2.0")
    assert not updates.is_newer("2.0", "2.0.0")
    assert not updates.is_newer("garbage", "2.0.0")
    assert updates.parse_version("v1.10.3-beta") == (1, 10, 3)


def test_icons(tmp_path):
    for color in ("live", "muted", "warning", "idle"):
        for glyph in ("mic", "headphones"):
            image = render(color, glyph, True)
            assert image.size == (64, 64) and image.mode == "RGBA"
            assert image.getpixel((0, 0))[3] == 0  # transparent corners
    save_ico(tmp_path / "app.ico")
    assert (tmp_path / "app.ico").stat().st_size > 1000


def test_single_instance_lock():
    from goxlr_discord_sync.system import SingleInstance

    first, second = SingleInstance(), SingleInstance()
    assert first.acquire()
    assert not second.acquire()
