"""System tray icon and menu (pystray runs its own thread)."""

import logging
import threading

import pystray

from . import APP_NAME, __version__, autostart, icons, paths

log = logging.getLogger(__name__)

AUTOSTART_LABEL = "Start with Windows" if paths.IS_WINDOWS else "Start on login"


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = pystray.Icon("goxlr_discord_sync", icons.render("idle"), APP_NAME, self._menu())
        self._thread: threading.Thread | None = None

    def _menu(self) -> pystray.Menu:
        app = self.app
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _: app.engine_summary(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("Cough button → Discord mute", lambda: app.toggle("cough_sync"),
                 checked=lambda _: app.cfg.cough_sync),
            item(lambda _: f"{app.cfg.deafen_channel} mute → Discord deafen", lambda: app.toggle("chat_sync"),
                 checked=lambda _: app.cfg.chat_sync),
            item("Two-way sync (Discord → GoXLR)", lambda: app.toggle("bidirectional"),
                 checked=lambda _: app.cfg.bidirectional),
            pystray.Menu.SEPARATOR,
            item("Status", lambda: app.show_status(), default=True),
            item("Settings…", lambda: app.open_settings()),
            item(AUTOSTART_LABEL, lambda: app.toggle_autostart(), checked=lambda _: _safe_autostart()),
            item("Authorize Discord again", lambda: app.reauthorize()),
            item("Open log", lambda: app.open_log()),
            item(lambda _: f"Install update v{app.update_info.version}…", lambda: app.install_update(),
                 visible=lambda _: app.can_self_update),
            item(lambda _: f"Update available: v{app.update_info.version}", lambda: app.open_update(),
                 visible=lambda _: app.update_info is not None and not app.can_self_update),
            item(f"About (v{__version__})", lambda: app.open_about()),
            pystray.Menu.SEPARATOR,
            item("Quit", lambda: app.quit()),
        )

    def start(self) -> None:
        self._thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
        self._thread.start()

    def refresh(self, spec: tuple[str, str, bool], summary: str) -> None:
        try:
            self.icon.icon = icons.render(*spec)
            # ASCII only: the X11 backend encodes the title as latin-1; 127 = Windows tooltip limit
            self.icon.title = f"{APP_NAME} - {summary}".encode("ascii", "replace").decode()[:127]
            self.icon.update_menu()
        except Exception:
            log.exception("Cannot update the tray icon")

    def notify(self, title: str, message: str) -> None:
        try:
            self.icon.notify(message, title)
        except Exception as e:
            log.info("Notification not shown (%s): %s - %s", e, title, message)

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass


def _safe_autostart() -> bool:
    try:
        return autostart.is_enabled()
    except OSError:
        return False
