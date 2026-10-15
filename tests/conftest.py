import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own data directory."""
    home = tmp_path / "home"
    monkeypatch.setenv("GOXLR_DISCORD_SYNC_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def english_ui():
    """Tests compare English texts; a test may switch to French."""
    from goxlr_discord_sync import i18n

    i18n.set_language("en")
    yield
    i18n.set_language("en")


@pytest.fixture(autouse=True)
def isolated_system(tmp_path):
    """Never touch the real autostart, Apps list entry or running app of the developer's machine.

    Tests run on Windows too: without this, remove_installation() deleted the real
    "Start with Windows" entry of an installed GoXLR Discord Sync. It uses its own
    MonkeyPatch so that a test calling monkeypatch.undo() cannot lift the isolation.
    """
    import uuid

    from goxlr_discord_sync import autostart, installer

    tag = uuid.uuid4().hex[:8]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(autostart, "VALUE_NAME", f"GoXLR Discord Sync TEST {tag}")
        mp.setattr(autostart, "legacy_vbs_path", lambda: tmp_path / "Startup" / autostart.LEGACY_VBS_NAME)
        mp.setattr(installer, "UNINSTALL_KEY", f"{installer.UNINSTALL_KEY}-TEST-{tag}")
        mp.setattr(installer, "stop_running_app", lambda: None)
        mp.setattr(installer, "stop_other_instances", lambda: None)
        mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        yield
        assert "TEST" in autostart.VALUE_NAME and "TEST" in installer.UNINSTALL_KEY
        if sys.platform == "win32":  # remove what a test may have left under the test names
            autostart.disable()
            installer.unregister_uninstall()
