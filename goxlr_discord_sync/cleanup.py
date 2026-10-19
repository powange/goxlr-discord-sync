"""Remove PyInstaller extraction folders left behind by earlier runs.

A onefile executable unpacks itself to %TEMP%\\_MEIxxxx and deletes the folder
on exit, but a forced stop (taskkill /F during an update, a crash, a power
cut) leaves about 40 MB behind for good. At startup the application removes
such folders when all of these hold:

* the folder belongs to GoXLR Discord Sync (marker file, or its signature);
* it is older than an hour (never a folder being unpacked right now);
* its Python runtime DLL (python3XY.dll) can be deleted: a running instance
  keeps that DLL loaded for its whole life and Windows refuses to delete a
  loaded DLL. (Renaming the folder is NOT a valid test: Windows allows it even
  while a DLL inside is loaded, see tests/test_windows.py.)

Windows only: elsewhere files in use can be deleted, so nothing proves that a
folder is unused (and /tmp is emptied at reboot anyway).
"""

import logging
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

MARKER = "goxlr_discord_sync.marker"
MIN_AGE = 3600


def is_ours(folder: Path) -> bool:
    if (folder / MARKER).exists() or (folder / "GoXLR_Discord_Sync.exe").exists():
        return True
    # Folders from versions built before the marker existed.
    return (folder / "websockets").is_dir() and (folder / "PIL").is_dir()


RUNTIME_DLL = re.compile(r"^python3\d+\.dll$", re.IGNORECASE)


def _release(folder: Path) -> bool:
    """Delete the runtime DLL; False when a running program still has it loaded."""
    dlls = [p for p in folder.iterdir() if RUNTIME_DLL.match(p.name)]
    for dll in dlls:
        try:
            dll.unlink()
        except OSError:
            return False
    return True  # without the runtime DLL no instance can be running from it


def cleanup_extraction_dirs(temp_dir: Path | None = None, current: str | None = None,
                            min_age: float = MIN_AGE, now: float | None = None) -> list[Path]:
    temp_dir = temp_dir or Path(tempfile.gettempdir())
    current = current if current is not None else getattr(sys, "_MEIPASS", None)
    now = now if now is not None else time.time()
    current_path = Path(current).resolve() if current else None
    removed = []
    for folder in temp_dir.glob("_MEI*"):
        try:
            if not folder.is_dir() or folder.resolve() == current_path:
                continue
            if now - folder.stat().st_mtime < min_age or not is_ours(folder):
                continue
            if not _release(folder):
                continue
        except OSError:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder)
    if removed:
        log.info("Removed %d leftover extraction folder(s): %s", len(removed), ", ".join(f.name for f in removed))
    return removed
