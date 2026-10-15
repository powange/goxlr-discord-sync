"""Synchronisation engine between GoXLR Utility and the Discord client.

Three independent tasks run side by side:

* the GoXLR task keeps a websocket session with GoXLR Utility and derives the
  wanted Discord mute/deafen state from the device status;
* the Discord task keeps the RPC connection (with authorization) and tracks
  Discord's actual voice settings through VOICE_SETTINGS_UPDATE events;
* the reconcile task pushes the wanted state to Discord whenever it changes.

A lost connection on one side never blocks the other side.
"""

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from websockets.exceptions import WebSocketException

from . import auth, secure_store
from .config import Config
from .discord_ipc import DiscordClosed, DiscordIPC, DiscordUnavailable, RPCError
from .goxlr import GoXLRClient, GoXLRStatus, MixerSnapshot
from .sync_logic import desired_flag, goxlr_state_for, plan_discord_update

log = logging.getLogger(__name__)

RETRY_MIN = 2
RETRY_MAX = 30
LOSS_NOTIFY_DELAY = 10  # seconds a connection must stay down before notifying


class NeedsUser(Exception):
    """Nothing more can happen until the user acts (setup or authorization)."""


@dataclass
class Status:
    goxlr_connected: bool = False
    discord_connected: bool = False
    auth: str = "unknown"  # unknown | not_configured | authorizing | required | ok
    goxlr_mute: bool | None = None   # wanted by the GoXLR
    goxlr_deaf: bool | None = None
    discord_mute: bool | None = None  # actually set in Discord
    discord_deaf: bool | None = None
    serial: str | None = None
    deafen_fader: str | None = None
    missing_device: str | None = None  # configured GoXLR serial that is not connected
    detail: str = "Starting..."
    last_latency_ms: float | None = None


class Engine:
    def __init__(
        self,
        cfg: Config,
        *,
        on_change: Callable[[], None] | None = None,
        notify: Callable[[str, str], None] | None = None,
        token_manager: auth.TokenManager | None = None,
        get_secret: Callable[[], str] = secure_store.get_client_secret,
        ipc_factory=DiscordIPC,
        goxlr_factory=GoXLRClient,
    ):
        self.cfg = cfg
        self.status = Status()
        self._on_change = on_change or (lambda: None)
        self._notify_cb = notify or (lambda title, message: None)
        self.tokens = token_manager or auth.TokenManager()
        self._get_secret = get_secret
        self._ipc_factory = ipc_factory
        self._goxlr_factory = goxlr_factory

        self._ipc: DiscordIPC | None = None
        self._goxlr: GoXLRClient | None = None
        self._snapshot: MixerSnapshot | None = None
        self._goxlr_status: GoXLRStatus | None = None
        self._may_prompt = True
        self._force_refresh = False
        self._auth_failures = 0
        self._set_in_flight = False      # a SET_VOICE_SETTINGS is waiting for its response
        self._events_during_set = False
        self._set_failures = 0
        self._retry_handle: asyncio.TimerHandle | None = None
        self._reconcile_fields: set[str] = set()
        # GoXLR state written by two-way sync: key -> (state we set, state to restore on unmute)
        self._pushed: dict[str, tuple[str, str]] = {}
        self._cancel_browser = threading.Event()
        self._tasks: set[asyncio.Task] = set()
        self._push_lock = asyncio.Lock()
        self._discord_wakeup = asyncio.Event()
        self._goxlr_restart = asyncio.Event()
        self._reconcile_needed = asyncio.Event()
        self._ever_connected: set[str] = set()
        self._loss_timers: dict[str, asyncio.TimerHandle] = {}
        self._loss_notified: set[str] = set()
        self._last_signature = None

    # --- lifecycle ----------------------------------------------------------

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._goxlr_loop(), name="goxlr")
                tg.create_task(self._discord_loop(), name="discord")
                tg.create_task(self._reconcile_loop(), name="reconcile")
        finally:
            self._cancel_browser.set()
            for timer in self._loss_timers.values():
                timer.cancel()
            if self._retry_handle:
                self._retry_handle.cancel()
            for task in list(self._tasks):
                task.cancel()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- public controls (call from the event loop thread) ------------------

    def apply_config(self, cfg: Config, credentials_changed: bool = False) -> None:
        old, self.cfg = self.cfg, cfg
        if credentials_changed or old.client_id != cfg.client_id or old.oauth_port != cfg.oauth_port:
            log.info("Discord application settings changed, reconnecting")
            if old.client_id != cfg.client_id:
                self.tokens.clear()  # tokens belong to the previous application
            self._may_prompt = True
            self._restart_discord()
        if old.goxlr_url != cfg.goxlr_url:
            log.info("GoXLR Utility address changed, reconnecting")
            self._goxlr_restart.set()
        if self._goxlr_status is not None:
            self._snapshot = self._goxlr_status.snapshot(cfg.mixer_serial, cfg.deafen_channel)
        # Re-enabling a sync pushes the current GoXLR state (other settings only
        # trigger a push when they change what the GoXLR wants).
        enabled = (cfg.cough_sync and not old.cough_sync) or (cfg.chat_sync and not old.chat_sync)
        self._recompute(force_reconcile=enabled)
        self._changed()

    def reauthorize(self) -> None:
        log.info("Re-authorization requested")
        self.tokens.clear()
        self._may_prompt = True
        self._auth_failures = 0
        self._restart_discord()

    def request_reconcile(self, fields: tuple[str, ...] = ("mute", "deaf")) -> None:
        self._reconcile_fields.update(fields)
        self._reconcile_needed.set()

    def _restart_discord(self) -> None:
        self._cancel_browser.set()  # stop a browser authorization for the old settings
        ipc = self._ipc
        if ipc is not None:
            self._spawn(ipc.close())
        self._discord_wakeup.set()

    # --- state helpers -------------------------------------------------------

    def _set(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self.status, key, value)
        self._changed()

    def _changed(self) -> None:
        signature = tuple(vars(self.status).values()) + (self.cfg.cough_sync, self.cfg.chat_sync)
        if signature != self._last_signature:
            self._last_signature = signature
            self._on_change()

    def _notify(self, title: str, message: str) -> None:
        if self.cfg.notifications:
            self._notify_cb(title, message)

    def _connection_changed(self, name: str, up: bool) -> None:
        if up:
            self._ever_connected.add(name)
            timer = self._loss_timers.pop(name, None)
            if timer:
                timer.cancel()
            if name in self._loss_notified:
                self._loss_notified.discard(name)
                self._notify(f"{name} reconnected", "Synchronisation is running again.")
        elif name in self._ever_connected and name not in self._loss_timers:
            loop = asyncio.get_running_loop()
            self._loss_timers[name] = loop.call_later(LOSS_NOTIFY_DELAY, self._loss_fire, name)

    def _loss_fire(self, name: str) -> None:
        self._loss_timers.pop(name, None)
        up = self.status.goxlr_connected if name == "GoXLR Utility" else self.status.discord_connected
        if not up:
            self._loss_notified.add(name)
            self._notify(f"{name} disconnected", "Mute synchronisation is paused until it comes back.")

    # --- GoXLR side ------------------------------------------------------------

    async def _goxlr_loop(self) -> None:
        delay = RETRY_MIN
        while True:
            self._goxlr_restart.clear()
            client = self._goxlr_factory(self.cfg.goxlr_url, self._on_goxlr_status)
            self._goxlr = client
            run_task = asyncio.create_task(client.run())
            restart_task = asyncio.create_task(self._goxlr_restart.wait())
            try:
                done, _ = await asyncio.wait({run_task, restart_task}, return_when=asyncio.FIRST_COMPLETED)
                if run_task in done:
                    run_task.result()
                    reason = "connection closed by GoXLR Utility"
                else:
                    reason = "restart requested"
            except (OSError, TimeoutError, WebSocketException) as e:
                reason = f"{e.__class__.__name__}: {e}"
            except Exception as e:
                log.exception("Unexpected GoXLR error")
                reason = str(e)
            finally:
                for task in (run_task, restart_task):
                    if not task.done():
                        task.cancel()
                        with contextlib.suppress(BaseException):
                            await task
                self._goxlr = None

            was_connected = self.status.goxlr_connected
            self._goxlr_status = None
            self._snapshot = None
            self._set(goxlr_connected=False, serial=None, deafen_fader=None)
            self._recompute()
            self._connection_changed("GoXLR Utility", False)
            if was_connected:
                log.warning("GoXLR Utility disconnected (%s)", reason)
                delay = RETRY_MIN
            else:
                log.info("GoXLR Utility not available (%s), retrying in %ss", reason, delay)
            if self._goxlr_restart.is_set():
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._goxlr_restart.wait(), delay)
            delay = min(delay * 2, RETRY_MAX)

    async def _on_goxlr_status(self, status: GoXLRStatus) -> None:
        self._goxlr_status = status
        previous = self._snapshot
        snap = status.snapshot(self.cfg.mixer_serial, self.cfg.deafen_channel)
        self._snapshot = snap
        if not self.status.goxlr_connected:
            serial = snap.serial if snap else "no device"
            log.info("Connected to GoXLR Utility (%s)", serial)
            self._set(goxlr_connected=True)
            self._connection_changed("GoXLR Utility", True)
            if snap and not snap.deafen_fader:
                log.info("Channel %s is not assigned to a fader: deafen sync inactive", self.cfg.deafen_channel)
        if previous and snap:
            if previous.cough_state != snap.cough_state:
                log.info("Cough button: %s -> %s (mute function %s)",
                         previous.cough_state, snap.cough_state, snap.cough_mute_type)
            if previous.deafen_fader != snap.deafen_fader:
                log.info("%s channel moved to fader %s", self.cfg.deafen_channel, snap.deafen_fader)
            elif previous.deafen_state != snap.deafen_state:
                log.info("%s fader: %s -> %s (mute function %s)", self.cfg.deafen_channel,
                         previous.deafen_state, snap.deafen_state, snap.deafen_mute_type)
        self._recompute()

    def _recompute(self, force_reconcile: bool = False) -> None:
        snap = self._snapshot
        cfg = self.cfg
        mute = deaf = None
        if snap is not None:
            mute = desired_flag(snap.cough_state, snap.cough_mute_type, cfg.mute_triggers)
            if snap.deafen_fader:
                deaf = desired_flag(snap.deafen_state, snap.deafen_mute_type, cfg.deafen_triggers)
        wanted_changed = (mute, deaf) != (self.status.goxlr_mute, self.status.goxlr_deaf)
        missing = cfg.mixer_serial if (cfg.mixer_serial and snap is None and self._goxlr_status is not None) else None
        if missing != self.status.missing_device:
            if missing:
                connected = ", ".join(sorted(self._goxlr_status.mixers)) or "none"
                log.warning("GoXLR %s is not connected (connected: %s): sync paused", missing, connected)
            elif self.status.missing_device:
                log.info("GoXLR %s is back", self.status.missing_device)
        self._set(
            missing_device=missing,
            goxlr_mute=mute,
            goxlr_deaf=deaf,
            serial=snap.serial if snap else None,
            deafen_fader=snap.deafen_fader if snap else None,
        )
        # Only GoXLR changes that matter trigger a push: volume moves must not
        # override a mute the user toggled directly in Discord.
        if wanted_changed or force_reconcile:
            self.request_reconcile()

    # --- Discord side ----------------------------------------------------------

    async def _discord_loop(self) -> None:
        delay = RETRY_MIN
        while True:
            self._discord_wakeup.clear()
            wait_for_user = False
            try:
                await self._discord_session()
            except NeedsUser as e:
                log.warning("%s", e)
                self._set(detail=str(e))
                wait_for_user = True
            except (DiscordUnavailable, DiscordClosed, RPCError, auth.TransientAuthError, OSError, TimeoutError) as e:
                if self.status.discord_connected:
                    log.warning("Discord connection lost: %s", e)
                else:
                    log.info("Discord not available: %s (retrying in %ss)", e, delay)
                self._set(detail=f"Discord: {e}")
            except Exception as e:
                log.exception("Unexpected Discord error")
                self._set(detail=f"Discord error: {e}")
            finally:
                was_connected = self.status.discord_connected
                self._set(discord_connected=False, discord_mute=None, discord_deaf=None)
                self._connection_changed("Discord", False)
                if was_connected:
                    delay = RETRY_MIN

            if wait_for_user:
                await self._discord_wakeup.wait()
                delay = RETRY_MIN
                continue
            if not self._discord_wakeup.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._discord_wakeup.wait(), delay)
            delay = min(delay * 2, RETRY_MAX)

    async def _discord_session(self) -> None:
        cfg = self.cfg
        secret = self._get_secret()
        if not cfg.client_id or not secret:
            self._set(auth="not_configured")
            raise NeedsUser("Setup required: enter your Discord application in Settings")

        ipc = self._ipc_factory(cfg.client_id, on_event=self._on_discord_event)
        self._ipc = ipc
        try:
            await ipc.connect()
            force, self._force_refresh = self._force_refresh, False
            token = await self.tokens.access_token(cfg.client_id, secret, force_refresh=force)
            if token is None:
                token = await self._interactive_authorize(ipc, cfg, secret)
            try:
                await ipc.authenticate(token)
            except RPCError as e:
                self._auth_failures += 1
                self._force_refresh = True
                if self._auth_failures >= 2:
                    log.warning("Discord keeps rejecting the token, a new authorization is needed")
                    self.tokens.clear()
                raise RPCError(e.code, f"Discord rejected the access token: {e.message}") from e
            self._auth_failures = 0

            voice = await ipc.get_voice_settings()
            await ipc.subscribe("VOICE_SETTINGS_UPDATE")
            self._update_discord_voice(voice)
            log.info("Connected to Discord")
            self._set(auth="ok", discord_connected=True, detail="Connected")
            self._connection_changed("Discord", True)
            self.request_reconcile()
            await ipc.wait_closed()
            raise DiscordClosed(ipc.close_reason or "closed")
        finally:
            self._ipc = None
            await ipc.close()

    async def _interactive_authorize(self, ipc: DiscordIPC, cfg: Config, secret: str) -> str:
        if not self._may_prompt:
            self._set(auth="required")
            raise NeedsUser("Discord authorization required: choose 'Authorize Discord' in the tray menu")
        self._may_prompt = False
        self._set(auth="authorizing", detail="Waiting for your approval in Discord")
        self._notify("Discord authorization", "Approve GoXLR Discord Sync in the Discord window.")
        try:
            code = await ipc.authorize(auth.SCOPES)
            return await self.tokens.exchange(cfg.client_id, secret, code, [cfg.redirect_uri, None])
        except (DiscordClosed, auth.TransientAuthError):
            # Not the user's answer: prompt again on the next attempt.
            self._may_prompt = True
            self._set(auth="unknown")
            raise
        except TimeoutError:
            self._set(auth="required")
            raise NeedsUser("The authorization request in Discord was not answered") from None
        except RPCError as e:
            text = e.message.lower()
            if "denied" in text or "cancel" in text or "reject" in text:
                self._set(auth="required")
                raise NeedsUser("Authorization declined in Discord. Use 'Authorize Discord' to try again.") from e
            log.warning("In-app authorization failed (%s), trying the browser", e)
        except auth.AuthError as e:
            log.warning("Code exchange failed (%s), trying the browser", e)

        self._set(detail="Waiting for authorization in your browser")
        self._notify("Discord authorization", "Approve GoXLR Discord Sync in your browser.")
        self._cancel_browser.clear()
        try:
            code = await asyncio.to_thread(
                auth.browser_authorize, cfg.client_id, cfg.redirect_uri, cfg.oauth_port, 180, self._cancel_browser
            )
            return await self.tokens.exchange(cfg.client_id, secret, code, [cfg.redirect_uri])
        except auth.TransientAuthError:
            self._may_prompt = True
            self._set(auth="unknown")
            raise
        except auth.AuthError as e:
            self._set(auth="required")
            raise NeedsUser(f"Discord authorization failed: {e}") from e
        finally:
            self._cancel_browser.set()

    def _update_discord_voice(self, data: dict) -> None:
        changes = {}
        if isinstance(data.get("mute"), bool):
            changes["discord_mute"] = data["mute"]
        if isinstance(data.get("deaf"), bool):
            changes["discord_deaf"] = data["deaf"]
        if changes:
            self._set(**changes)

    def _on_discord_event(self, evt: str, data: dict) -> None:
        if evt != "VOICE_SETTINGS_UPDATE":
            return
        st = self.status
        prev_mute, prev_deaf = st.discord_mute, st.discord_deaf
        self._update_discord_voice(data)
        if self._set_in_flight:
            # Echo of our own change (Discord may send it before the response), or a click
            # at the same instant: settled by the reconcile loop once the response is in.
            self._events_during_set = True
            return
        # Only fields that actually changed come from the user; everything else is a repeat.
        deaf_changed = prev_deaf is not None and st.discord_deaf != prev_deaf
        mute_changed = prev_mute is not None and st.discord_mute != prev_mute
        if deaf_changed and not st.discord_deaf:
            # Undeafen: Discord restores its own pre-deafen mute, which says nothing about
            # what the GoXLR wants. Re-apply the GoXLR mute (never leave a live mic behind).
            self.request_reconcile(("mute",))
        if not self.cfg.bidirectional:
            return
        # A mute change implied by deafening/undeafening is not a user choice.
        push_deaf = deaf_changed
        push_mute = mute_changed and not deaf_changed and not st.discord_deaf
        if push_deaf or push_mute:
            self._spawn(self._push_discord_to_goxlr(push_mute=push_mute, push_deaf=push_deaf))

    # --- reconciliation ----------------------------------------------------------

    async def _reconcile_loop(self) -> None:
        while True:
            await self._reconcile_needed.wait()
            self._reconcile_needed.clear()
            fields, self._reconcile_fields = self._reconcile_fields, set()
            try:
                await self._reconcile(fields)
            except Exception:
                log.exception("Synchronisation failed")

    def _schedule_retry(self, fields: set[str]) -> None:
        delay = min(RETRY_MIN * 2 ** self._set_failures, RETRY_MAX)
        self._set_failures += 1
        if self._retry_handle:
            self._retry_handle.cancel()
        self._retry_handle = asyncio.get_running_loop().call_later(delay, self.request_reconcile, tuple(fields))
        log.info("Retrying the Discord update in %ss", delay)

    async def _reconcile(self, fields: set[str]) -> None:
        for _ in range(4):  # undeafen, then mute, plus a re-check after events during a set
            ipc = self._ipc
            if ipc is None or not self.status.discord_connected:
                return
            want_mute = self.status.goxlr_mute if self.cfg.cough_sync and "mute" in fields else None
            want_deaf = self.status.goxlr_deaf if self.cfg.chat_sync and "deaf" in fields else None
            args = plan_discord_update(want_mute, want_deaf, self.status.discord_mute, self.status.discord_deaf)
            if not args:
                self._set_failures = 0
                return
            started = time.monotonic()
            self._set_in_flight = True
            self._events_during_set = False
            try:
                result = await ipc.set_voice_settings(**args)
            except DiscordClosed as e:
                log.warning("Discord closed before applying %s: %s", args, e)
                return  # the new session pushes the state again
            except (RPCError, TimeoutError) as e:
                log.warning("Discord did not apply %s: %s", args, e)
                self._set(detail=f"Sync failed: {e}")
                self._schedule_retry(fields)
                return
            finally:
                self._set_in_flight = False
            latency = (time.monotonic() - started) * 1000
            if isinstance(result, dict) and ("mute" in result or "deaf" in result):
                self._update_discord_voice(result)
            else:
                self._update_discord_voice(args)
            log.info("Discord %s (%.0f ms)", ", ".join(f"{k}={v}" for k, v in args.items()), latency)
            self._set(last_latency_ms=latency, detail="Synchronised")
            if self._events_during_set:
                # Something else may have changed meanwhile: the GoXLR state decides.
                fields = {"mute", "deaf"}

    async def _push_discord_to_goxlr(self, push_mute: bool, push_deaf: bool) -> None:
        """Two-way sync: mirror a change the user made in Discord onto the GoXLR."""
        async with self._push_lock:
            client, snap, st, cfg = self._goxlr, self._snapshot, self.status, self.cfg
            if client is None or snap is None or not client.connected:
                return
            try:
                if (push_deaf and cfg.chat_sync and snap.deafen_fader and st.discord_deaf is not None
                        and st.goxlr_deaf is not None and st.discord_deaf != st.goxlr_deaf):
                    fader = snap.deafen_fader
                    await self._push_state(
                        client, snap.serial, "fader", st.discord_deaf, snap.deafen_state, snap.deafen_mute_type,
                        cfg.deafen_triggers, lambda state: {"SetFaderMuteState": [fader, state]},
                    )
                if (push_mute and cfg.cough_sync and not st.discord_deaf and st.discord_mute is not None
                        and st.goxlr_mute is not None and st.discord_mute != st.goxlr_mute):
                    await self._push_state(
                        client, snap.serial, "cough", st.discord_mute, snap.cough_state, snap.cough_mute_type,
                        cfg.mute_triggers, lambda state: {"SetCoughMuteState": state},
                    )
            except (ConnectionError, RuntimeError, TimeoutError, WebSocketException) as e:
                log.warning("Could not update the GoXLR: %s", e)

    async def _push_state(self, client, serial, key, muted, current, mute_type, triggers, command) -> None:
        if muted:
            target = goxlr_state_for(True, mute_type, triggers)
            if target is None:
                log.info("No GoXLR mute state matches the Discord change with the current settings")
                return
            # Remember a mute that was not synced (e.g. Muted to Stream) to restore it later.
            self._pushed[key] = (target, current if current and current != "Unmuted" else "Unmuted")
        else:
            pushed = self._pushed.pop(key, None)
            target = pushed[1] if pushed and pushed[0] == current else "Unmuted"
        log.info("Discord %s changed: setting %s to %s", "deafen" if key == "fader" else "mute", key, target)
        await client.send_command(serial, command(target))

    # --- presentation --------------------------------------------------------------

    def summary(self) -> str:
        st = self.status
        if st.auth == "not_configured":
            return "Setup required"
        if st.auth == "authorizing":
            return "Waiting for Discord authorization"
        if st.auth == "required":
            return "Discord authorization required"
        if not st.discord_connected:
            return "Waiting for Discord"
        if not st.goxlr_connected:
            return "Waiting for GoXLR Utility"
        if st.missing_device:
            return f"GoXLR {st.missing_device} not connected"
        if st.serial is None:
            return "No GoXLR connected"
        mic = "Mic muted" if st.discord_mute else "Mic live"
        audio = "Deafened" if st.discord_deaf else "Hearing"
        return f"{mic}, {audio.lower()}"

    def icon_spec(self) -> tuple[str, str, bool]:
        """(colour, glyph, slashed) for icons.render()."""
        st = self.status
        if st.auth in ("not_configured", "required", "authorizing"):
            return "idle", "mic", False
        if st.discord_connected:
            mute, deaf = st.discord_mute, st.discord_deaf
        else:
            mute, deaf = st.goxlr_mute, st.goxlr_deaf
        if deaf:
            spec = ("muted", "headphones", True)
        elif mute:
            spec = ("muted", "mic", True)
        else:
            spec = ("live", "mic", False)
        if not (st.discord_connected and st.goxlr_connected) or st.serial is None:
            return "warning", spec[1], spec[2]
        return spec

    def details(self) -> str:
        st = self.status
        if st.goxlr_connected and st.serial:
            goxlr = f"connected ({st.serial})"
        elif st.missing_device:
            goxlr = f"connected, but GoXLR {st.missing_device} is not plugged in"
        else:
            goxlr = "connected, no device" if st.goxlr_connected else "not available"
        discord = "connected" if st.discord_connected else self.summary().lower()
        lines = [f"GoXLR Utility: {goxlr}", f"Discord: {discord}"]
        if st.discord_connected:
            lines.append(f"Mic: {'muted' if st.discord_mute else 'live'} · "
                         f"Audio: {'deafened' if st.discord_deaf else 'hearing'}")
        if st.goxlr_connected and self.cfg.chat_sync:
            where = f"fader {st.deafen_fader}" if st.deafen_fader else "not on a fader"
            lines.append(f"{self.cfg.deafen_channel} channel: {where}")
        if st.last_latency_ms is not None:
            lines.append(f"Last sync: {st.last_latency_ms:.0f} ms")
        return "\n".join(lines)
