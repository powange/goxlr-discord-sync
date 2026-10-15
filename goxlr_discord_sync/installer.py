"""File operations of the setup: install, update with rollback, locate an installation.

Kept free of Tk so it can be tested; setup_gui.py drives it.
"""

import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from . import APP_NAME, REPO_URL, autostart, config, paths, secure_store, system
from .i18n import tr

log = logging.getLogger(__name__)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
START_TIMEOUT = 45  # seconds for a new version to report that it started (onefile unpack + antivirus scan)
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GoXLRDiscordSync"


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


# --- "Add or remove programs" ---------------------------------------------------

def register_uninstall(exe: Path, version: str) -> None:
    """Per-user entry in Settings > Apps; the application exe is its own uninstaller."""
    if not paths.IS_WINDOWS:
        return
    import winreg

    strings = {
        "DisplayName": APP_NAME,
        "DisplayVersion": version,
        "Publisher": "powange",
        "DisplayIcon": f"{exe},0",
        "InstallLocation": str(exe.parent),
        "UninstallString": subprocess.list2cmdline([str(exe), "--uninstall"]),
        "URLInfoAbout": REPO_URL,
        "HelpLink": f"{REPO_URL}/issues",
    }
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in strings.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name in ("NoModify", "NoRepair"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
        if exe.exists():
            winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, exe.stat().st_size // 1024)


def unregister_uninstall() -> None:
    if not paths.IS_WINDOWS:
        return
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass


def delete_file_with_retry(exe: Path) -> bool:
    for _ in range(10):
        try:
            exe.unlink(missing_ok=True)
            return True
        except PermissionError:
            time.sleep(0.5)
    return False


def schedule_self_delete(exe: Path) -> bool:
    """Delete a running executable (and its folder if empty) once this process has exited."""
    if not paths.IS_WINDOWS:
        return delete_file_with_retry(exe)

    def quoted(path: Path) -> str:
        return "'" + str(path).replace("'", "''") + "'"

    script = (
        f"$exe = {quoted(exe)}; $dir = {quoted(exe.parent)}; "
        "for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 500; "
        "Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue; "
        "if (-not (Test-Path -LiteralPath $exe)) { break } }; "
        "if ((Test-Path -LiteralPath $dir) -and -not (Get-ChildItem -LiteralPath $dir -Force)) "
        "{ Remove-Item -LiteralPath $dir -Force -ErrorAction SilentlyContinue }"
    )
    # CREATE_NO_WINDOW, not DETACHED_PROCESS: PowerShell exits at once without a console.
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        creationflags=NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True,
    )
    return True


def remove_installation(exe: Path | None, remove_settings: bool,
                        delete_exe: Callable[[Path], bool] = delete_file_with_retry) -> list[str]:
    """Shared by the setup's Uninstall button and GoXLR_Discord_Sync.exe --uninstall."""
    report = []
    try:
        autostart.disable()
        report.append("✓ " + tr("Autostart removed"))
    except OSError as e:
        report.append("⚠ " + tr("Autostart: {error}", error=e))
    try:
        unregister_uninstall()
    except OSError as e:
        report.append("⚠ " + tr("Apps list entry: {error}", error=e))
    if exe is not None and exe.name.lower() == paths.APP_EXE_NAME.lower() and exe.exists():
        (exe.parent / "requirements.txt").unlink(missing_ok=True)  # left by setup 1.x
        if delete_exe(exe):
            if exe.exists():  # deleted after this process exits
                report.append("✓ " + tr("Application removed from {path}", path=exe.parent))
            else:
                try:
                    exe.parent.rmdir()  # only when empty
                except OSError:
                    pass
                report.append("✓ " + tr("Application removed from {path}", path=exe.parent))
        else:
            report.append("⚠ " + tr("Could not delete {path}", path=exe))
    elif exe is not None:
        report.append(tr("Application not found in {path}", path=exe.parent))
    if remove_settings:
        secure_store.clear_all()
        shutil.rmtree(paths.data_dir(), ignore_errors=True)
        report.append("✓ " + tr("Settings and credentials deleted"))
    else:
        report.append(tr("Settings kept in {path}", path=paths.data_dir()))
    return report


def stop_other_instances() -> None:
    """Stop the running application, but not the current process (--uninstall)."""
    if not paths.IS_WINDOWS:
        return
    import os

    subprocess.run(["taskkill", "/F", "/IM", paths.APP_EXE_NAME, "/FI", f"PID ne {os.getpid()}"],
                   capture_output=True, timeout=15, creationflags=NO_WINDOW, check=False)


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
        emit(tr("Stopping the application…"))
        stop()
        copy_with_retry(bundled, target)

    emit(tr("Starting the new version…"))
    launch([str(target)])
    emit(tr("Checking that the new version starts…"))
    if started(version):
        backup.unlink(missing_ok=True)
        log.info("Updated to %s", version)
        return

    log.error("Version %s did not start, restoring the previous version", version)
    if not backup.exists():
        raise UpdateRolledBack(tr("Version {version} did not start and no previous version was kept.",
                                  version=version))
    emit(tr("The new version did not start. Restoring the previous version…"))
    stop()
    copy_with_retry(backup, target)
    backup.unlink(missing_ok=True)
    launch([str(target)])
    raise UpdateRolledBack(tr("Version {version} did not start, so the previous version was restored.",
                              version=version))
