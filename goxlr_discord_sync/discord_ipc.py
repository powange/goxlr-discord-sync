"""Minimal asyncio client for Discord's local RPC (IPC) socket.

Replaces pypresence, whose async client mixes subscribed events and command
responses in the same read buffer and cannot wait long enough for the user to
answer an AUTHORIZE prompt.
"""

import asyncio
import json
import logging
import os
import struct
import sys
import tempfile
import uuid
from collections.abc import Callable

log = logging.getLogger(__name__)

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = range(5)
_HEADER = struct.Struct("<II")


class DiscordUnavailable(Exception):
    """Discord is not running, or its RPC socket refused the connection."""


class DiscordClosed(Exception):
    """The RPC connection was closed."""


class RPCError(Exception):
    def __init__(self, code: int | None, message: str):
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message


def ipc_paths() -> list[str]:
    if sys.platform == "win32":
        return [rf"\\?\pipe\discord-ipc-{i}" for i in range(10)]
    bases = []
    for var in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        if os.environ.get(var):
            bases.append(os.environ[var])
    bases += [tempfile.gettempdir(), "/tmp"]
    candidates = []
    for base in dict.fromkeys(bases):
        for sub in ("", "app/com.discordapp.Discord", "snap.discord", ".flatpak/dev.vencord.Vesktop/xdg-run"):
            for i in range(10):
                candidates.append(os.path.join(base, sub, f"discord-ipc-{i}"))
    return candidates


class DiscordIPC:
    def __init__(self, client_id: str, on_event: Callable[[str, dict], None] | None = None):
        self.client_id = str(client_id)
        self.on_event = on_event
        self._reader: asyncio.StreamReader | None = None
        self._writer = None
        self._read_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ready: asyncio.Future | None = None
        self._closed = asyncio.Event()
        self.close_reason = ""

    # --- connection -------------------------------------------------------

    async def _open(self, path: str) -> None:
        loop = asyncio.get_running_loop()
        if sys.platform == "win32":
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            transport, _ = await loop.create_pipe_connection(lambda: protocol, path)
            self._reader = reader
            self._writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        else:
            self._reader, self._writer = await asyncio.open_unix_connection(path)

    async def connect(self, timeout: float = 10) -> dict:
        """Open the socket, handshake and wait for READY. Returns READY data."""
        last_error: Exception | None = None
        for path in ipc_paths():
            if sys.platform != "win32" and not os.path.exists(path):
                continue
            try:
                await self._open(path)
                break
            except OSError as e:
                last_error = e
        else:
            raise DiscordUnavailable(f"Discord is not running ({last_error or 'no IPC socket found'})")

        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        # Mark a failed handshake as retrieved even if nobody awaits it any more.
        self._ready.add_done_callback(lambda f: f.cancelled() or f.exception())
        self._read_task = asyncio.create_task(self._read_loop(), name="discord-ipc-reader")
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
        try:
            return await asyncio.wait_for(asyncio.shield(self._ready), timeout)
        except TimeoutError:
            await self.close()
            raise DiscordUnavailable("Discord did not answer the RPC handshake") from None

    def _send(self, op: int, payload: dict) -> None:
        if self._writer is None or self._closed.is_set():
            raise DiscordClosed(self.close_reason or "not connected")
        data = json.dumps(payload).encode("utf-8")
        self._writer.write(_HEADER.pack(op, len(data)) + data)

    async def _read_loop(self) -> None:
        try:
            while True:
                header = await self._reader.readexactly(_HEADER.size)
                op, length = _HEADER.unpack(header)
                body = await self._reader.readexactly(length)
                payload = json.loads(body.decode("utf-8")) if body else {}
                if op == OP_FRAME:
                    self._dispatch(payload)
                elif op == OP_PING:
                    self._send(OP_PONG, payload)
                elif op == OP_CLOSE:
                    self.close_reason = f"{payload.get('message', 'closed by Discord')} (code {payload.get('code')})"
                    break
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as e:
            self.close_reason = self.close_reason or f"connection lost ({e.__class__.__name__})"
        except ValueError as e:
            self.close_reason = f"invalid data from Discord: {e}"
        except asyncio.CancelledError:
            self.close_reason = self.close_reason or "closed"
            raise
        finally:
            self._mark_closed()

    def _dispatch(self, payload: dict) -> None:
        evt = payload.get("evt")
        nonce = payload.get("nonce")
        data = payload.get("data") or {}
        if nonce and nonce in self._pending:
            fut = self._pending.pop(nonce)
            if fut.done():
                return
            if evt == "ERROR":
                fut.set_exception(RPCError(data.get("code"), data.get("message", "unknown error")))
            else:
                fut.set_result(data)
            return
        if evt == "READY":
            if self._ready and not self._ready.done():
                self._ready.set_result(data)
            return
        if evt == "ERROR" and self._ready and not self._ready.done():
            self._ready.set_exception(RPCError(data.get("code"), data.get("message", "handshake refused")))
            return
        if payload.get("cmd") == "DISPATCH" and evt and self.on_event:
            try:
                self.on_event(evt, data)
            except Exception:
                log.exception("Discord event handler failed for %s", evt)

    def _mark_closed(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        error = DiscordClosed(self.close_reason or "closed")
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(error)
        self._pending.clear()
        if self._ready and not self._ready.done():
            self._ready.set_exception(DiscordUnavailable(self.close_reason or "closed during handshake"))
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass

    @property
    def is_open(self) -> bool:
        return self._writer is not None and not self._closed.is_set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        if self.is_open:
            try:
                self._send(OP_CLOSE, {"v": 1, "client_id": self.client_id})
            except Exception:
                pass
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):
                pass
        self._mark_closed()

    # --- commands ---------------------------------------------------------

    async def command(self, cmd: str, args: dict | None = None, evt: str | None = None,
                      timeout: float = 10) -> dict:
        nonce = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self._pending[nonce] = fut
        payload = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            payload["evt"] = evt
        try:
            self._send(OP_FRAME, payload)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(nonce, None)

    async def authorize(self, scopes: list[str], timeout: float = 300) -> str:
        """Show Discord's in-app authorization prompt and return the OAuth2 code."""
        data = await self.command("AUTHORIZE", {"client_id": self.client_id, "scopes": scopes},
                                  timeout=timeout)
        code = data.get("code")
        if not code:
            raise RPCError(None, "Discord returned no authorization code")
        return code

    async def authenticate(self, access_token: str) -> dict:
        return await self.command("AUTHENTICATE", {"access_token": access_token})

    async def get_voice_settings(self) -> dict:
        return await self.command("GET_VOICE_SETTINGS")

    async def set_voice_settings(self, **settings) -> dict:
        return await self.command("SET_VOICE_SETTINGS", settings)

    async def subscribe(self, evt: str, args: dict | None = None) -> dict:
        return await self.command("SUBSCRIBE", args, evt=evt)

    async def get_selected_voice_channel(self) -> dict | None:
        return await self.command("GET_SELECTED_VOICE_CHANNEL")
