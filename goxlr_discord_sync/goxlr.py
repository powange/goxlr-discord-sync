"""GoXLR Utility websocket client and status model."""

import asyncio
import copy
import itertools
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import websockets

log = logging.getLogger(__name__)

# Large enough for a full DaemonStatus with several devices and file lists.
MAX_MESSAGE_SIZE = 16 * 1024 * 1024


class PatchError(Exception):
    pass


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def apply_json_patch(doc: dict, ops: list[dict]) -> None:
    """Apply RFC 6902 add/replace/remove operations in place."""
    for op in ops:
        kind = op.get("op")
        path = op.get("path", "")
        if not path.startswith("/"):
            raise PatchError(f"Unsupported path {path!r}")
        tokens = [_unescape(t) for t in path[1:].split("/")]
        parent = doc
        try:
            for token in tokens[:-1]:
                if isinstance(parent, list):
                    parent = parent[int(token)]
                elif isinstance(parent, dict) and token in parent:
                    parent = parent[token]
                else:
                    raise PatchError(f"Path not found: {path}")
        except (ValueError, IndexError) as e:
            raise PatchError(f"Path not found: {path}") from e
        last = tokens[-1]
        try:
            if kind in ("add", "replace"):
                value = op.get("value")
                if isinstance(parent, list):
                    if last == "-":
                        parent.append(value)
                    elif kind == "add":
                        parent.insert(int(last), value)
                    else:
                        parent[int(last)] = value
                else:
                    parent[last] = value
            elif kind == "remove":
                if isinstance(parent, list):
                    del parent[int(last)]
                else:
                    del parent[last]
            else:
                raise PatchError(f"Unsupported operation {kind!r}")
        except (KeyError, IndexError, ValueError, TypeError) as e:
            raise PatchError(f"Cannot apply {kind} at {path}: {e}") from e


@dataclass(frozen=True)
class MixerSnapshot:
    """The parts of one GoXLR's state that matter for Discord."""

    serial: str
    cough_state: str | None
    cough_mute_type: str | None
    deafen_fader: str | None  # "A".."D", None when the channel is not on a fader
    deafen_state: str | None
    deafen_mute_type: str | None
    buttons_down: frozenset[str] = frozenset()  # GoXLR buttons currently pressed


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


class GoXLRStatus:
    """Full DaemonStatus document, kept up to date with patches."""

    def __init__(self, status: dict):
        self.doc = copy.deepcopy(status)

    def apply_patch(self, ops: list[dict]) -> None:
        apply_json_patch(self.doc, ops)

    @property
    def mixers(self) -> dict:
        mixers = self.doc.get("mixers")
        return mixers if isinstance(mixers, dict) else {}

    def pick_serial(self, preferred: str = "") -> str | None:
        """The configured device, or the first one when none is configured.

        A configured device that is not connected gives None: syncing another
        GoXLR instead would mute the wrong person's microphone.
        """
        mixers = self.mixers
        if preferred:
            return preferred if preferred in mixers else None
        return next(iter(sorted(mixers)), None)

    def snapshot(self, preferred_serial: str, deafen_channel: str) -> MixerSnapshot | None:
        serial = self.pick_serial(preferred_serial)
        if serial is None:
            return None
        mixer = _as_dict(self.mixers[serial])
        cough = _as_dict(mixer.get("cough_button"))
        fader_name = fader = None
        for name, info in sorted(_as_dict(mixer.get("fader_status")).items()):
            if isinstance(info, dict) and info.get("channel") == deafen_channel:
                fader_name, fader = name, info
                break
        return MixerSnapshot(
            serial=serial,
            cough_state=cough.get("state"),
            cough_mute_type=cough.get("mute_type"),
            deafen_fader=fader_name,
            deafen_state=fader.get("mute_state") if fader else None,
            deafen_mute_type=fader.get("mute_type") if fader else None,
            buttons_down=frozenset(name for name, down in _as_dict(mixer.get("button_down")).items() if down is True),
        )


class GoXLRClient:
    """One websocket session with GoXLR Utility.

    ``on_status`` is called (and awaited) with the current GoXLRStatus every
    time the full status is received or a patch is applied. It must return
    quickly: the reconciliation with Discord happens elsewhere.
    """

    def __init__(self, url: str, on_status: Callable[[GoXLRStatus], Awaitable[None]]):
        self.url = url
        self.on_status = on_status
        self.status: GoXLRStatus | None = None
        self._ws = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}

    @property
    def connected(self) -> bool:
        return self._ws is not None and self.status is not None

    async def run(self) -> None:
        """Connect and process messages until the connection closes."""
        async with websockets.connect(self.url, open_timeout=5, max_size=MAX_MESSAGE_SIZE) as ws:
            self._ws = ws
            try:
                await self._request_status()
                async for raw in ws:
                    await self._handle(raw)
            finally:
                self._ws = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("GoXLR Utility connection closed"))
                self._pending.clear()

    async def _request_status(self) -> None:
        await self._ws.send(json.dumps({"id": next(self._ids), "data": "GetStatus"}))

    async def _handle(self, raw) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            log.warning("Ignoring non-JSON message from GoXLR Utility")
            return
        if not isinstance(message, dict):
            return
        data = message.get("data")
        if isinstance(data, dict) and "Patch" in data:
            if self.status is None:
                return  # the full status is on its way
            try:
                self.status.apply_patch(data["Patch"])
            except PatchError as e:
                log.warning("Could not apply GoXLR patch (%s), reloading full status", e)
                await self._request_status()
                return
            await self.on_status(self.status)
        elif isinstance(data, dict) and "Status" in data:
            self.status = GoXLRStatus(data["Status"])
            await self.on_status(self.status)
        else:
            fut = self._pending.pop(message.get("id"), None)
            if fut is not None and not fut.done():
                fut.set_result(data)

    async def send_command(self, serial: str, command: dict, timeout: float = 5) -> None:
        """Send a GoXLRCommand, e.g. {"SetCoughMuteState": "MutedToAll"}."""
        if self._ws is None:
            raise ConnectionError("GoXLR Utility is not connected")
        request_id = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        try:
            await self._ws.send(json.dumps({"id": request_id, "data": {"Command": [serial, command]}}))
            result = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(request_id, None)  # no leak when the answer never comes
        if isinstance(result, dict) and "Error" in result:
            raise RuntimeError(f"GoXLR Utility refused {command}: {result['Error']}")
