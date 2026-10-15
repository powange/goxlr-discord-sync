"""Operating-system helpers: single instance lock, dialogs, opening files."""

import logging
import os
import subprocess
import sys

from . import APP_NAME, paths

log = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\GoXLRDiscordSync.SingleInstance"
_ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Held for the whole process lifetime; acquire() is False if another copy runs."""

    def __init__(self):
        self._handle = None

    def acquire(self) -> bool:
        if paths.IS_WINDOWS:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
            if not handle:
                return True  # cannot tell: do not block startup
            if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
                return False
            self._handle = handle
            return True
        import fcntl

        lock = open(paths.data_dir() / "instance.lock", "w")  # noqa: SIM115 (kept open on purpose)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            return False
        self._handle = lock
        return True


def message_box(text: str, title: str = APP_NAME, error: bool = False, ask: bool = False) -> bool:
    """Native dialog usable before any GUI exists. With ask=True returns the Yes/No answer."""
    if paths.IS_WINDOWS:
        import ctypes

        flags = (0x10 if error else 0x40) | (0x04 if ask else 0x00) | 0x00010000  # MB_SETFOREGROUND
        result = ctypes.windll.user32.MessageBoxW(None, text, title, flags)
        return result == 6  # IDYES
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        try:
            if ask:
                return bool(messagebox.askyesno(title, text, parent=root))
            (messagebox.showerror if error else messagebox.showinfo)(title, text, parent=root)
            return False
        finally:
            root.destroy()
    except Exception:
        log.warning("%s: %s", title, text)
        return False


def open_path(path) -> None:
    path = str(path)
    if paths.IS_WINDOWS:
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def launch_detached(command: list[str]) -> None:
    kwargs = {}
    if paths.IS_WINDOWS:
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, close_fds=True, **kwargs)


def fix_std_streams() -> None:
    """pythonw/--windowed builds have no stdout/stderr; send them to devnull."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
    if sys.stdin is None:
        sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115


def has_console() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False
