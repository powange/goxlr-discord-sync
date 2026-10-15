"""Collect every text shown through tr() (used by test_i18n.py)."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [*sorted((ROOT / "goxlr_discord_sync").glob("*.py")), ROOT / "setup_gui.py"]


def _literal(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def translatable_texts() -> dict[str, str]:
    """{english text: "file:line"} for tr("...") calls and the label tables."""
    texts = {}
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args):
                continue
            if node.func.id == "tr" or (node.func.id == "text" and path.name == "tray.py"):
                value = _literal(node.args[0])
                if value is not None:
                    texts.setdefault(value, f"{path.name}:{node.lineno}")
    from goxlr_discord_sync import config, i18n

    for table in (config.MUTE_TARGET_LABELS, config.ACTION_BUTTONS, config.BUTTON_ACTIONS, i18n.LANGUAGES):
        for value in table.values():
            texts.setdefault(value, "label table")
    texts.setdefault("Start with Windows", "tray.py")  # tray.AUTOSTART_LABEL (pystray needs a display)
    texts.setdefault("Start on login", "tray.py")
    return texts


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    for text in translatable_texts():
        print(repr(text))
