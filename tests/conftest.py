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
