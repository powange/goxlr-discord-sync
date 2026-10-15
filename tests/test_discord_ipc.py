import asyncio
import json
import struct
import sys

import pytest

from goxlr_discord_sync.discord_ipc import DiscordClosed, DiscordIPC, DiscordUnavailable, RPCError

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses a Unix socket")


def frame(op, payload):
    data = json.dumps(payload).encode()
    return struct.pack("<II", op, len(data)) + data


class FakeDiscord:
    def __init__(self):
        self.voice = {"mute": False, "deaf": False, "input": {"volume": 100}}
        self.writers = []
        self.received = []

    async def handle(self, reader, writer):
        self.writers.append(writer)
        try:
            op, length = struct.unpack("<II", await reader.readexactly(8))
            hello = json.loads(await reader.readexactly(length))
            if hello["client_id"] == "bad":
                writer.write(frame(2, {"code": 4000, "message": "Invalid Client ID"}))
                await writer.drain()
                writer.close()
                return
            writer.write(frame(1, {"cmd": "DISPATCH", "evt": "READY", "data": {"v": 1}, "nonce": None}))
            while True:
                op, length = struct.unpack("<II", await reader.readexactly(8))
                msg = json.loads(await reader.readexactly(length))
                if op == 2:
                    break
                if op == 4:
                    self.pongs = getattr(self, "pongs", 0) + 1
                    continue
                self.received.append(msg)
                reply = {"cmd": msg["cmd"], "nonce": msg["nonce"], "evt": None, "data": {}}
                if msg["cmd"] == "AUTHENTICATE":
                    if msg["args"]["access_token"] != "good":
                        reply.update(evt="ERROR", data={"code": 4009, "message": "Invalid access token"})
                elif msg["cmd"] == "AUTHORIZE":
                    reply["data"] = {"code": "the-code"}
                elif msg["cmd"] == "GET_VOICE_SETTINGS":
                    reply["data"] = dict(self.voice)
                elif msg["cmd"] == "SET_VOICE_SETTINGS":
                    self.voice.update(msg["args"])
                    # Discord sends the update event before the command response.
                    writer.write(frame(1, {"cmd": "DISPATCH", "evt": "VOICE_SETTINGS_UPDATE",
                                           "data": dict(self.voice), "nonce": None}))
                    writer.write(frame(3, {"ping": 1}))
                    reply["data"] = dict(self.voice)
                elif msg["cmd"] == "SUBSCRIBE":
                    reply["evt"] = msg["evt"]
                writer.write(frame(1, reply))
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()


@pytest.fixture
async def fake_discord(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    fake = FakeDiscord()
    server = await asyncio.start_unix_server(fake.handle, path=str(tmp_path / "discord-ipc-0"))
    yield fake
    for w in fake.writers:
        w.close()
    server.close()


async def test_commands_and_events_are_not_mixed(fake_discord):
    events = []
    ipc = DiscordIPC("123", on_event=lambda evt, data: events.append((evt, data)))
    await ipc.connect()
    await ipc.authenticate("good")
    await ipc.subscribe("VOICE_SETTINGS_UPDATE")
    result = await ipc.set_voice_settings(mute=True)
    assert result["mute"] is True
    assert (await ipc.get_voice_settings())["mute"] is True
    assert events == [("VOICE_SETTINGS_UPDATE", {"mute": True, "deaf": False, "input": {"volume": 100}})]
    assert await ipc.authorize(["rpc"]) == "the-code"
    assert fake_discord.pongs == 1  # PING answered
    await ipc.close()
    assert not ipc.is_open


async def test_error_response_raises(fake_discord):
    ipc = DiscordIPC("123")
    await ipc.connect()
    with pytest.raises(RPCError) as info:
        await ipc.authenticate("expired")
    assert info.value.code == 4009
    await ipc.close()


async def test_server_close_is_detected(fake_discord):
    ipc = DiscordIPC("123")
    await ipc.connect()
    for w in fake_discord.writers:
        w.close()
    await asyncio.wait_for(ipc.wait_closed(), 2)
    with pytest.raises(DiscordClosed):
        await ipc.get_voice_settings()


async def test_invalid_client_id(fake_discord):
    ipc = DiscordIPC("bad")
    with pytest.raises(DiscordUnavailable, match="Invalid Client ID"):
        await ipc.connect(timeout=2)


async def test_discord_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr("goxlr_discord_sync.discord_ipc.ipc_paths", lambda: [str(tmp_path / "discord-ipc-0")])
    with pytest.raises(DiscordUnavailable):
        await DiscordIPC("123").connect()
