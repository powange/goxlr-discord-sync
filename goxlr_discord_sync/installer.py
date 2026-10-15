"""File operations of the setup: install, update with rollback, locate an installation.

Kept free of Tk so it can be tested; setup_gui.py drives it.
"""

import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from . import autostart, config, paths, system

log = logging.getLogger(__name__)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
START_TIMEOUT = 45  # seconds for a new version to report that it started (onefile unpack + antivirus scan)


class UpdateRolledBack(Exception):
    """The new version did not start; the previous one was restored."""


def stop_running_app() -> None:
    """Stop the installed application so its executable can be replaced or deleted."""
    if not paths.IS_WINDOWS:
        return
    subprocess.run(["taskkill", "/F", "/IM", paths.APP_EXE_NAME], capture_output=True,
                   timeout=15, creationflags=NO_WINDOW, check=False)


def copy_with_retry(source: Path, target: Path, attempts: int = 10) -> None:
    """Copy next to the target, then swap: an interrupted copy never leaves a broken exe.

    The swap is retried while the target is locked (the running app is exiting).
    """
    staged = target.with_name(target.name + ".new")
    shutil.copy2(source, staged)
    try:
        for attempt in range(attempts):
            try:
                staged.replace(target)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(0.5)
    finally:
        staged.unlink(missing_ok=True)


def installed_dir() -> Path | None:
    """Folder of the current installation, from its autostart entry."""
    try:
        exe = autostart.installed_exe_path()
    except OSError:
        return None
    if exe and exe.name.lower() == paths.APP_EXE_NAME.lower() and exe.exists():
        return exe.parent
    return None


def app_installed() -> bool:
    try:
        return autostart.is_enabled() or (paths.default_install_dir() / paths.APP_EXE_NAME).exists()
    except OSError:
        return False


# --- start report (written by the application, read by the updater) -------------

def record_started(version: str) -> None:
    """Called by the application as soon as it holds its single-instance lock."""
    state = config.load_state()
    state["started_version"] = version
    config.save_state(state)


def _clear_started() -> None:
    state = config.load_state()
    state.pop("started_version", None)
    config.save_state(state)


def wait_for_start(version: str, timeout: float = START_TIMEOUT, poll: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if config.load_state().get("started_version") == version:
            return True
        time.sleep(poll)
    return False


# --- update ------------------------------------------------------------------------

def perform_update(
    bundled: Path,
    target: Path,
    version: str,
    emit: Callable[[str], None] = lambda text: None,
    launch: Callable[[list[str]], None] = system.launch_detached,
    stop: Callable[[], None] = stop_running_app,
    started: Callable[[str], bool] = wait_for_start,
) -> None:
    """Replace target by bundled, start it, and roll back if it does not start.

    Raises UpdateRolledBack after restoring the previous version.
    """
    backup = target.with_name(target.name + ".previous")
    if target.exists():
        shutil.copy2(target, backup)  # reading a running exe is allowed
    _clear_started()
    try:
        copy_with_retry(bundled, target, attempts=20)  # the app quits by itself
    except PermissionError:
        emit("Stopping the application…")
        stop()
        copy_with_retry(bundled, target)

    emit("Starting the new version…")
    launch([str(target)])
    emit("Checking that the new version starts…")
    if started(version):
        backup.unlink(missing_ok=True)
        log.info("Updated to %s", version)
        return

    log.error("Version %s did not start, restoring the previous version", version)
    if not backup.exists():
        raise UpdateRolledBack(f"Version {version} did not start and no previous version was kept.")
    emit("The new version did not start. Restoring the previous version…")
    stop()
    copy_with_retry(backup, target)
    backup.unlink(missing_ok=True)
    launch([str(target)])
    raise UpdateRolledBack(f"Version {version} did not start, so the previous version was restored.")
