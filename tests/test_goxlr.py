import asyncio
import json

import pytest
import websockets

from goxlr_discord_sync.goxlr import GoXLRClient, GoXLRStatus, PatchError, apply_json_patch

from .factories import mixer, status


def test_apply_json_patch_operations():
    doc = {"a": {"b": 1, "x/y": 2, "list": [1, 2]}}
    apply_json_patch(doc, [
        {"op": "replace", "path": "/a/b", "value": 5},
        {"op": "replace", "path": "/a/x~1y", "value": 3},
        {"op": "add", "path": "/a/c", "value": {"d": 1}},
        {"op": "add", "path": "/a/list/-", "value": 3},
        {"op": "remove", "path": "/a/list/0"},
    ])
    assert doc == {"a": {"b": 5, "x/y": 3, "c": {"d": 1}, "list": [2, 3]}}


def test_apply_json_patch_errors():
    with pytest.raises(PatchError):
        apply_json_patch({}, [{"op": "replace", "path": "/missing/key", "value": 1}])
    with pytest.raises(PatchError):
        apply_json_patch({"a": 1}, [{"op": "move", "from": "/a", "path": "/b"}])


def test_snapshot_follows_fader_reassignment():
    st = GoXLRStatus(status(S1=mixer()))
    snap = st.snapshot("", "Chat")
    assert (snap.serial, snap.deafen_fader, snap.deafen_mute_type) == ("S1", "B", "ToPhones")
    st.apply_patch([
        {"op": "replace", "path": "/mixers/S1/fader_status/B/channel", "value": "Game"},
        {"op": "replace", "path": "/mixers/S1/fader_status/D/channel", "value": "Chat"},
    ])
    assert st.snapshot("", "Chat").deafen_fader == "D"
    st.apply_patch([{"op": "replace", "path": "/mixers/S1/fader_status/D/channel", "value": "Music"}])
    snap = st.snapshot("", "Chat")
    assert snap.deafen_fader is None and snap.deafen_state is None


def test_snapshot_picks_configured_device():
    st = GoXLRStatus(status(S2=mixer(cough="MutedToAll"), S1=mixer()))
    assert st.snapshot("", "Chat").serial == "S1"  # stable default
    assert st.snapshot("S2", "Chat").cough_state == "MutedToAll"
    assert st.snapshot("unknown", "Chat").serial == "S1"
    assert GoXLRStatus({"mixers": {}}).snapshot("", "Chat") is None


class FakeUtility:
    """Speaks the GoXLR Utility websocket protocol."""

    def __init__(self):
        self.status = status()
        self.commands = []
        self.clients = []

    async def handler(self, ws):
        self.clients.append(ws)
        async for raw in ws:
            msg = json.loads(raw)
            if msg["data"] == "GetStatus":
                await ws.send(json.dumps({"id": msg["id"], "data": {"Status": self.status}}))
            elif "Command" in msg["data"]:
                self.commands.append(msg["data"]["Command"])
                reply = {"Error": "nope"} if "Bad" in json.dumps(msg["data"]) else "Ok"
                await ws.send(json.dumps({"id": msg["id"], "data": reply}))

    async def patch(self, ops):
        for ws in self.clients:
            await ws.send(json.dumps({"id": 0, "data": {"Patch": ops}}))


async def test_client_status_patches_and_commands():
    utility = FakeUtility()
    seen = []
    got_update = asyncio.Event()

    async def on_status(st):
        seen.append(st.snapshot("", "Chat").cough_state)
        got_update.set()

    async with websockets.serve(utility.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = GoXLRClient(f"ws://127.0.0.1:{port}/api/websocket", on_status)
        task = asyncio.create_task(client.run())
        await asyncio.wait_for(got_update.wait(), 5)
        got_update.clear()
        await utility.patch([{"op": "replace", "path": "/mixers/S1/cough_button/state", "value": "MutedToX"}])
        await asyncio.wait_for(got_update.wait(), 5)
        assert seen == ["Unmuted", "MutedToX"]

        await client.send_command("S1", {"SetCoughMuteState": "Unmuted"})
        assert utility.commands == [["S1", {"SetCoughMuteState": "Unmuted"}]]
        with pytest.raises(RuntimeError):
            await client.send_command("S1", {"Bad": 1})

        # A patch that does not apply triggers a full status reload.
        got_update.clear()
        await utility.patch([{"op": "replace", "path": "/mixers/S9/x", "value": 1}])
        await asyncio.wait_for(got_update.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_malformed_patch_paths_are_patch_errors():
    with pytest.raises(PatchError):
        apply_json_patch({"a": [1]}, [{"op": "replace", "path": "/a/x/b", "value": 1}])
    with pytest.raises(PatchError):
        apply_json_patch({"a": [1]}, [{"op": "replace", "path": "/a/5/b", "value": 1}])


def test_snapshot_tolerates_unexpected_types():
    st = GoXLRStatus({"mixers": {"S1": {"cough_button": None, "fader_status": "?"}}})
    snap = st.snapshot("", "Chat")
    assert snap.cough_state is None and snap.deafen_fader is None


async def test_command_timeout_does_not_leak():
    utility = FakeUtility()

    async def silent(ws):
        async for raw in ws:
            if json.loads(raw)["data"] == "GetStatus":
                await ws.send(json.dumps({"id": 1, "data": {"Status": utility.status}}))

    ready = asyncio.Event()

    async def on_status(st):
        ready.set()

    async with websockets.serve(silent, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = GoXLRClient(f"ws://127.0.0.1:{port}/", on_status)
        task = asyncio.create_task(client.run())
        await asyncio.wait_for(ready.wait(), 5)
        for _ in range(3):
            with pytest.raises(TimeoutError):
                await client.send_command("S1", {"SetCoughMuteState": "Unmuted"}, timeout=0.05)
        assert client._pending == {}
        task.cancel()
