"""Application entry point: wires the engine, the tray icon and the settings files."""

import argparse
import asyncio
import dataclasses
import logging
import os
import threading
import time
import webbrowser

from . import (
    APP_NAME,
    REPO_URL,
    __version__,
    autostart,
    config,
    logging_setup,
    migration,
    paths,
    secure_store,
    system,
    updates,
)
from .engine import Engine

log = logging.getLogger(__name__)

UPDATE_INTERVAL = 24 * 3600


class App:
    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.loop: asyncio.AbstractEventLoop | None = None
        self.engine: Engine | None = None
        self.tray = None
        self.update_info: tuple[str, str] | None = None
        self._main_task: asyncio.Task | None = None

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        from .tray import Tray  # imported late: pystray needs a desktop session

        self.loop = asyncio.get_running_loop()
        self._main_task = asyncio.current_task()
        self.engine = Engine(self.cfg, on_change=self._refresh_tray, notify=self._notify)
        self.tray = Tray(self)
        self.tray.start()
        self._restore_update_info()
        self._refresh_tray()
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.engine.run(), name="engine")
                tg.create_task(self._watch_files(), name="config-watcher")
                tg.create_task(self._update_loop(), name="updates")
        except asyncio.CancelledError:
            log.info("Shutting down")
        finally:
            self.tray.stop()

    # --- engine callbacks (event loop thread) ----------------------------------

    def _refresh_tray(self) -> None:
        if self.tray is not None and self.engine is not None:
            self.tray.refresh(self.engine.icon_spec(), self.engine.summary())

    def _notify(self, title: str, message: str) -> None:
        if self.tray is not None:
            self.tray.notify(title, message)

    def engine_summary(self) -> str:
        return self.engine.summary() if self.engine else "Starting..."

    def _apply(self, cfg: config.Config, credentials_changed: bool = False) -> None:
        self.cfg = cfg
        self.engine.apply_config(cfg, credentials_changed)
        self._refresh_tray()

    async def _watch_files(self) -> None:
        """Pick up changes written by the settings window (or by hand)."""
        cfg_mtime = paths.mtime(paths.config_file())
        secrets_mtime = paths.mtime(paths.secrets_file())
        secret = await asyncio.to_thread(secure_store.get_client_secret)
        while True:
            await asyncio.sleep(2)
            new_cfg_mtime = paths.mtime(paths.config_file())
            new_secrets_mtime = paths.mtime(paths.secrets_file())
            credentials_changed = False
            if new_secrets_mtime != secrets_mtime:
                secrets_mtime = new_secrets_mtime
                new_secret = await asyncio.to_thread(secure_store.get_client_secret)
                credentials_changed = new_secret != secret
                secret = new_secret
            if new_cfg_mtime != cfg_mtime or credentials_changed:
                cfg_mtime = new_cfg_mtime
                new_cfg = config.load()
                if new_cfg != self.cfg or credentials_changed:
                    log.info("Settings reloaded")
                    self._apply(new_cfg, credentials_changed)

    # --- updates -----------------------------------------------------------------

    def _restore_update_info(self) -> None:
        state = config.load_state()
        version, url = state.get("latest_version"), state.get("latest_url")
        if version and url and updates.is_newer(version, __version__):
            self.update_info = (version, url)

    async def _update_loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            state = config.load_state()
            if self.cfg.check_updates and time.time() - state.get("last_update_check", 0) >= UPDATE_INTERVAL:
                latest = await asyncio.to_thread(updates.fetch_latest_release)
                state["last_update_check"] = time.time()
                if latest:
                    state["latest_version"], state["latest_url"] = latest
                    if updates.is_newer(latest[0], __version__):
                        self.update_info = latest
                        self._refresh_tray()
                        if state.get("notified_version") != latest[0]:
                            state["notified_version"] = latest[0]
                            self._notify("Update available", f"Version {latest[0]} is available. "
                                         "Use the tray menu to download it.")
                config.save_state(state)
            await asyncio.sleep(3600)

    # --- tray actions (called from the tray thread) --------------------------------

    def _in_loop(self, func, *args) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(func, *args)

    def toggle(self, field: str) -> None:
        def _do():
            cfg = dataclasses.replace(self.cfg, **{field: not getattr(self.cfg, field)})
            config.save(cfg)
            log.info("%s set to %s", field, getattr(cfg, field))
            self._apply(cfg)
        self._in_loop(_do)

    def toggle_autostart(self) -> None:
        try:
            if autostart.is_enabled():
                autostart.disable()
            else:
                autostart.enable()
        except OSError as e:
            log.error("Cannot change autostart: %s", e)
            self._notify("Autostart", f"Could not change the setting: {e}")
        self._in_loop(self._refresh_tray)

    def show_status(self) -> None:
        if self.engine is not None:
            self._notify(APP_NAME, self.engine.details())

    def open_settings(self) -> None:
        system.launch_detached(paths.self_command("--settings"))

    def reauthorize(self) -> None:
        self._in_loop(self.engine.reauthorize)

    def open_log(self) -> None:
        system.open_path(paths.log_file())

    def open_update(self) -> None:
        if self.update_info:
            webbrowser.open(self.update_info[1])

    def open_about(self) -> None:
        webbrowser.open(REPO_URL)

    def quit(self) -> None:
        log.info("Quit requested")
        # Safety net if a blocking call keeps the loop from finishing.
        watchdog = threading.Timer(5, os._exit, args=(0,))
        watchdog.daemon = True
        watchdog.start()
        if self.loop is not None and self._main_task is not None:
            self.loop.call_soon_threadsafe(self._main_task.cancel)


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="GoXLR_Discord_Sync", description=APP_NAME)
    parser.add_argument("--settings", action="store_true", help="open the settings window")
    parser.add_argument("--autostart", choices=("on", "off"), help="register or remove the session autostart")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def main(argv=None) -> None:
    system.fix_std_streams()
    args = _parse_args(argv)

    if args.settings:
        logging_setup.setup("settings.log", console=system.has_console())
        from .settings_window import run_settings

        run_settings()
        return
    if args.autostart:
        logging_setup.setup(console=True)
        (autostart.enable if args.autostart == "on" else autostart.disable)()
        return

    logging_setup.setup(console=system.has_console())
    log.info("%s %s starting", APP_NAME, __version__)

    instance = system.SingleInstance()
    if not instance.acquire():
        log.info("Another instance is already running")
        system.message_box(f"{APP_NAME} is already running.\n\nLook for its icon in the system tray.")
        return

    try:
        migration.run()
    except Exception:
        log.exception("Migration of version 1.x files failed")

    cfg = config.load()
    if not cfg.client_id or not secure_store.get_client_secret():
        log.warning("Discord application not configured")
        if system.message_box(
            f"{APP_NAME} needs your Discord application details before it can work.\n\n"
            "Open the settings now?",
            ask=True,
        ):
            system.launch_detached(paths.self_command("--settings"))

    try:
        App(cfg).run()
    except Exception as e:
        log.exception("Fatal error")
        system.message_box(f"{APP_NAME} stopped because of an unexpected error:\n\n{e}\n\n"
                           f"Details are in {paths.log_file()}", error=True)
        raise
