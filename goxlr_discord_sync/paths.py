"""File locations and small file helpers.

Binaries live wherever the user installed them; configuration, secrets and
logs live in the per-user data directory so the install folder can be
read-only (e.g. Program Files).
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_FROZEN = bool(getattr(sys, "frozen", False))

APP_EXE_NAME = "GoXLR_Discord_Sync.exe"
SCRIPT_NAME = "goxlr_discord_sync.pyw"


def data_dir() -> Path:
    override = os.environ.get("GOXLR_DISCORD_SYNC_HOME")
    if override:
        path = Path(override)
    elif IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        path = Path(base) / "GoXLR Discord Sync"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        path = Path(base) / "goxlr-discord-sync"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file() -> Path:
    return log_dir() / "app.log"


def config_file() -> Path:
    return data_dir() / "config.json"


def secrets_file() -> Path:
    return data_dir() / "secrets.dat"


def state_file() -> Path:
    return data_dir() / "state.json"


def app_dir() -> Path:
    """Directory holding the executable (frozen) or the source checkout."""
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "GoXLR Discord Sync"


def pythonw_executable() -> str:
    """pythonw.exe next to the current interpreter when it exists (no console window)."""
    exe = Path(sys.executable)
    if IS_WINDOWS:
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def self_command(*args: str) -> list[str]:
    """Command line that starts this application again with extra arguments."""
    if IS_FROZEN:
        return [sys.executable, *args]
    return [pythonw_executable(), str(app_dir() / SCRIPT_NAME), *args]


def atomic_write_bytes(path: Path, data: bytes, private: bool = False) -> None:
    """Write through a temporary file so a crash never leaves a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        if private and not IS_WINDOWS:
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _replace_with_retry(src: str, dst: Path, attempts: int = 20) -> None:
    """os.replace, retried: on Windows it fails while another process (antivirus,
    indexer, the settings window) has the destination open."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05)


def read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: dict) -> None:
    atomic_write_bytes(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))


def mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None
