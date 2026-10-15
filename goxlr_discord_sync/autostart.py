"""Start-with-session registration (HKCU Run key on Windows, XDG autostart elsewhere)."""

import logging
import os
import shlex
import subprocess
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "GoXLR Discord Sync"
LEGACY_VBS_NAME = "GoXLR_Discord_Sync.vbs"


def legacy_vbs_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / LEGACY_VBS_NAME


def _desktop_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "goxlr-discord-sync.desktop"


def default_command() -> list[str]:
    return paths.self_command()


def _to_command_line(command: list[str]) -> str:
    if paths.IS_WINDOWS:
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def remove_legacy_vbs() -> None:
    vbs = legacy_vbs_path()
    if vbs and vbs.exists():
        try:
            vbs.unlink()
            log.info("Removed legacy startup script %s", vbs)
        except OSError as e:
            log.warning("Cannot remove %s: %s", vbs, e)


def registered_command() -> str | None:
    if paths.IS_WINDOWS:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, VALUE_NAME)
                return str(value)
        except OSError:
            return None
    desktop = _desktop_file()
    try:
        for line in desktop.read_text(encoding="utf-8").splitlines():
            if line.startswith("Exec="):
                return line[len("Exec="):]
    except OSError:
        pass
    return None


def is_enabled() -> bool:
    if registered_command():
        return True
    vbs = legacy_vbs_path()
    return bool(paths.IS_WINDOWS and vbs and vbs.exists())


def enable(command: list[str] | None = None) -> None:
    command_line = _to_command_line(command or default_command())
    if paths.IS_WINDOWS:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command_line)
        remove_legacy_vbs()
    else:
        desktop = _desktop_file()
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(
            "[Desktop Entry]\nType=Application\nName=GoXLR Discord Sync\n"
            f"Exec={command_line}\nX-GNOME-Autostart-enabled=true\nTerminal=false\n",
            encoding="utf-8",
        )
    log.info("Autostart enabled: %s", command_line)


def disable() -> None:
    if paths.IS_WINDOWS:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
        remove_legacy_vbs()
    else:
        try:
            _desktop_file().unlink()
        except FileNotFoundError:
            pass
    log.info("Autostart disabled")


def installed_exe_path() -> Path | None:
    """Executable referenced by the current autostart entry (used by the uninstaller)."""
    command = registered_command()
    if command:
        try:
            first = command.split('"')[1] if command.startswith('"') else command.split()[0]
            return Path(first)
        except IndexError:
            return None
    vbs = legacy_vbs_path()
    if vbs and vbs.exists():
        import re

        match = re.search(r'WshShell\.Run\s+"""([^"]+)"""', vbs.read_text(errors="ignore"))
        if match:
            return Path(match.group(1))
    return None
