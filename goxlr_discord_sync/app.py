"""Application entry point: wires the engine, the tray icon and the settings files."""

import argparse
import asyncio
import dataclasses
import logging
import os
import sys
import threading
import time
import webbrowser

from . import (
    APP_NAME,
    REPO_URL,
    __version__,
    autostart,
    config,
    installer,
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
        self.update_info: updates.Release | None = None
        self._updating = False
        self._check_lock = asyncio.Lock()
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
            self._announce_new_version()
        except OSError as e:
            log.warning("Cannot record the running version: %s", e)
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
        seen = (paths.mtime(paths.config_file()), paths.mtime(paths.secrets_file()),
                await asyncio.to_thread(secure_store.get_client_secret))
        while True:
            await asyncio.sleep(2)
            try:
                seen = await self._reload_if_changed(*seen)
            except Exception:
                # This task shares the app TaskGroup: an escaping error would stop the app.
                log.exception("Cannot reload the settings")

    async def _reload_if_changed(self, cfg_mtime, secrets_mtime, secret):
        new_cfg_mtime = paths.mtime(paths.config_file())
        new_secrets_mtime = paths.mtime(paths.secrets_file())
        credentials_changed = False
        if new_secrets_mtime != secrets_mtime:
            new_secret = await asyncio.to_thread(secure_store.get_client_secret)
            credentials_changed = new_secret != secret
            secrets_mtime, secret = new_secrets_mtime, new_secret
        if new_cfg_mtime != cfg_mtime or credentials_changed:
            cfg_mtime = new_cfg_mtime
            new_cfg = config.load()
            if new_cfg != self.cfg or credentials_changed:
                log.info("Settings reloaded")
                self._apply(new_cfg, credentials_changed)
        return cfg_mtime, secrets_mtime, secret

    # --- updates -----------------------------------------------------------------

    def _restore_update_info(self) -> None:
        release = updates.Release.from_dict(config.load_state().get("latest_release"))
        if release and updates.is_newer(release.version, __version__):
            self.update_info = release

    def _announce_new_version(self) -> None:
        """Tell the user once that an update was installed."""
        state = config.load_state()
        previous = state.get("last_run_version")
        if previous != __version__:
            if previous and updates.is_newer(__version__, previous):
                self._notify("Update installed", f"{APP_NAME} is now version {__version__}.")
            state["last_run_version"] = __version__
            config.save_state(state)

    async def _update_loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            last = config.load_state().get("last_update_check", 0)
            if self.cfg.check_updates and time.time() - last >= UPDATE_INTERVAL:
                await self.check_updates()
            await asyncio.sleep(3600)

    async def _fetch_release(self) -> updates.Release | None:
        return await asyncio.to_thread(updates.fetch_latest_release)

    async def check_updates(self, manual: bool = False) -> None:
        """Daily check, or "Check for updates" from the menu (manual=True).

        Never raises: it runs in the app TaskGroup, where an error would stop the app.
        """
        async with self._check_lock:  # a click during the daily check waits for it
            try:
                await self._check_updates(manual)
            except Exception as e:
                log.exception("Update check failed")
                if manual:
                    self._notify("Update check failed", str(e))

    async def _check_updates(self, manual: bool) -> None:
        latest = await self._fetch_release()
        state = config.load_state()
        state["last_update_check"] = time.time()
        if latest is None:
            config.save_state(state)
            if manual:
                self._notify("Update check failed", "GitHub could not be reached. Try again later.")
            return
        state["latest_release"] = latest.to_dict()
        if not updates.is_newer(latest.version, __version__):
            self.update_info = None
            config.save_state(state)
            self._refresh_tray()
            if manual:
                self._notify("No update available", f"You're running the latest version ({__version__}).")
            return
        self.update_info = latest
        self._refresh_tray()
        first_notice = state.get("notified_version") != latest.version
        state["notified_version"] = latest.version
        config.save_state(state)
        if manual and self.can_self_update:
            self.install_update()  # the confirmation dialog is the answer
        elif manual or first_notice:
            action = "Install update" if self.can_self_update else "Update available"
            self._notify("Update available", f"Version {latest.version} is available. "
                         f"Choose “{action}” in the tray menu.")

    def check_updates_now(self) -> None:
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.check_updates(manual=True), self.loop)

    @property
    def can_self_update(self) -> bool:
        """Installed executables update through the release installer; source checkouts use git."""
        return paths.IS_FROZEN and paths.IS_WINDOWS and bool(self.update_info and self.update_info.installable)

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
            webbrowser.open(self.update_info.page_url)

    def install_update(self) -> None:
        """Ask for confirmation, download the installer and hand over to it."""
        if self._updating or not self.update_info:
            return
        self._updating = True
        threading.Thread(target=self._install_update, args=(self.update_info,), name="update", daemon=True).start()

    def _install_update(self, release: updates.Release) -> None:
        try:
            notes = updates.notes_excerpt(release.notes)
            question = (
                f"{APP_NAME} {release.version} is available (you have {__version__}).\n\n"
                + (f"{notes}\n\n" if notes else "")
                + "Install it now? The application closes for a few seconds and starts again on its own."
            )
            if not system.message_box(question, title=f"{APP_NAME} update", ask=True):
                log.info("Update to %s postponed by the user", release.version)
                return
            self._notify("Downloading update", f"Downloading version {release.version}…")
            installer = updates.download_setup(release)
            log.info("Starting the installer to update to %s", release.version)
            system.launch_detached([str(installer), "--update", "--target", sys.executable])
            self.quit()
        except (updates.UpdateError, OSError) as e:
            log.error("Update failed: %s", e)
            if system.message_box(f"The update could not be installed:\n\n{e}\n\n"
                                  "Open the download page instead?", title=f"{APP_NAME} update",
                                  error=True, ask=True):
                webbrowser.open(release.page_url)
        finally:
            self._updating = False

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
        installer.record_started(__version__)  # tells a running update that this version starts
    except OSError as e:
        log.warning("Cannot record the start: %s", e)

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
