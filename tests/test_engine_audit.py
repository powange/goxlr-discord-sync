"""Regression tests for the v2 audit findings (hot mic, echo handling, retries)."""

import asyncio

from goxlr_discord_sync import engine as engine_mod
from goxlr_discord_sync.config import Config
from goxlr_discord_sync.discord_ipc import RPCError
from goxlr_discord_sync.engine import Engine

from .factories import mixer, status
from .test_engine import FakeGoXLR, FakeIPC, FakeTokens, connected, until


class EventFirstIPC(FakeIPC):
    """Discord emits VOICE_SETTINGS_UPDATE before answering the command."""
    async def set_voice_settings(self, **args):
        result = await super().set_voice_settings(**args)
        self.on_event("VOICE_SETTINGS_UPDATE", dict(self.voice))
        await asyncio.sleep(0.05)  # RPC latency for the response
        return result


class FailingIPC(FakeIPC):
    fail = True
    async def set_voice_settings(self, **args):
        if FailingIPC.fail:
            FailingIPC.fail = False
            raise RPCError(1000, "transient")
        return await super().set_voice_settings(**args)


def make(ipc_cls, cfg):
    FakeIPC.instances.clear()
    FakeGoXLR.instances.clear()
    eng = Engine(cfg, token_manager=FakeTokens(), get_secret=lambda: "s",
                 ipc_factory=ipc_cls, goxlr_factory=FakeGoXLR)
    return eng, asyncio.create_task(eng.run())


def cough(serial, state):
    return [{"op": "replace", "path": f"/mixers/{serial}/cough_button/state", "value": state}]


def chat(state):
    return [{"op": "replace", "path": "/mixers/S1/fader_status/B/mute_state", "value": state}]


async def test_hot_mic_after_undeafen_in_discord_two_way():
    eng, t = make(FakeIPC, Config(client_id="1", bidirectional=True))
    try:
        ipc, gx = await connected(eng)
        await gx.patch(chat("MutedToX"))          # deafen via Chat fader
        await until(lambda: ipc.voice["deaf"])
        await gx.patch(cough("S1", "MutedToX"))   # user mutes mic on GoXLR while deafened
        await asyncio.sleep(0.1)
        ipc.user_changes(deaf=False, mute=False)  # user undeafens in Discord; Discord restores pre-deafen mute=False
        await asyncio.sleep(0.2)
        assert gx.status.doc["mixers"]["S1"]["cough_button"]["state"] == "MutedToX", "cough mute was undone -> hot mic"
    finally:
        t.cancel()


async def test_hot_mic_after_undeafen_in_discord_one_way():
    eng, t = make(FakeIPC, Config(client_id="1", bidirectional=False))
    try:
        ipc, gx = await connected(eng)
        await gx.patch(chat("MutedToX"))
        await until(lambda: ipc.voice["deaf"])
        await gx.patch(cough("S1", "MutedToX"))
        await asyncio.sleep(0.1)
        ipc.user_changes(deaf=False, mute=False)
        await asyncio.sleep(0.2)
        assert ipc.voice["mute"] is True, "GoXLR cough is muted but Discord mic is live"
    finally:
        t.cancel()


async def test_event_before_response_undeafen_unmutes_goxlr():
    # fader deafen -> cough mute while deafened -> fader undeafen: no Discord click at all
    eng, t = make(EventFirstIPC, Config(client_id="1", bidirectional=True))
    try:
        ipc, gx = await connected(eng)
        await gx.patch(chat("MutedToX"))
        await until(lambda: ipc.voice["deaf"])
        await asyncio.sleep(0.1)  # leave the echo window
        await gx.patch(cough("S1", "MutedToX"))
        await asyncio.sleep(0.1)
        await gx.patch(chat("Unmuted"))
        await asyncio.sleep(0.5)
        assert gx.status.doc["mixers"]["S1"]["cough_button"]["state"] == "MutedToX"
    finally:
        t.cancel()


async def test_event_before_response_rapid_toggle():
    eng, t = make(EventFirstIPC, Config(client_id="1", bidirectional=True))
    try:
        ipc, gx = await connected(eng)
        await asyncio.sleep(0.1)
        await gx.patch(cough("S1", "MutedToX"))
        await asyncio.sleep(0.01)                 # set mute=True in flight
        await gx.patch(cough("S1", "Unmuted"))    # user releases / toggles back quickly
        await asyncio.sleep(0.6)
        assert gx.status.doc["mixers"]["S1"]["cough_button"]["state"] == "Unmuted"
        assert ipc.voice["mute"] is False
    finally:
        t.cancel()


async def test_failed_set_is_retried(monkeypatch):
    monkeypatch.setattr(engine_mod, "RETRY_MIN", 0.05)
    FailingIPC.fail = True
    eng, t = make(FailingIPC, Config(client_id="1"))
    try:
        ipc, gx = await connected(eng)
        FailingIPC.fail = True
        await gx.patch(cough("S1", "MutedToX"))
        await asyncio.sleep(0.3)
        assert ipc.voice["mute"] is True, "GoXLR muted, Discord stays live, no retry"
    finally:
        t.cancel()


async def test_user_change_right_after_a_sync_is_kept():
    eng, t = make(FakeIPC, Config(client_id="1", bidirectional=True))
    try:
        ipc, gx = await connected(eng)
        await gx.patch(cough("S1", "MutedToX"))
        await until(lambda: ipc.voice["mute"])
        await asyncio.sleep(0.3)
        ipc.user_changes(mute=False)   # user clicks unmute in Discord 0.3 s later
        await asyncio.sleep(0.3)
        assert gx.status.doc["mixers"]["S1"]["cough_button"]["state"] == "Unmuted"
    finally:
        t.cancel()


class SlowEventIPC(FakeIPC):
    async def set_voice_settings(self, **args):
        result = await super().set_voice_settings(**args)
        snap = dict(self.voice)
        await asyncio.sleep(0.03)
        self.on_event("VOICE_SETTINGS_UPDATE", snap)
        await asyncio.sleep(0.02)
        return result


async def test_rapid_toggle_event_between():
    eng, t = make(SlowEventIPC, Config(client_id="1", bidirectional=True))
    try:
        ipc, gx = await connected(eng)
        await asyncio.sleep(0.1)
        await gx.patch(cough("S1", "MutedToX"))
        await asyncio.sleep(0.01)
        await gx.patch(cough("S1", "Unmuted"))
        await asyncio.sleep(0.5)
        assert gx.status.doc["mixers"]["S1"]["cough_button"]["state"] == "Unmuted"
    finally:
        t.cancel()


async def test_rapid_toggle_event_between_hot_mic():
    eng, t = make(SlowEventIPC, Config(client_id="1", bidirectional=True))
    try:
        ipc, gx = await connected(eng, status(S1=mixer(cough="MutedToX")))
        await until(lambda: ipc.voice["mute"])
        await asyncio.sleep(0.1)
        await gx.patch(cough("S1", "Unmuted"))
        await asyncio.sleep(0.01)
        await gx.patch(cough("S1", "MutedToX"))   # user re-mutes immediately
        await asyncio.sleep(0.5)
        assert ipc.voice["mute"] is True
    finally:
        t.cancel()


async def test_two_way_discards_stream_mute():
    eng, t = make(FakeIPC, Config(client_id="1", bidirectional=True))
    try:
        ipc, gx = await connected(eng, status(S1=mixer(cough="MutedToX", cough_type="ToStream")))
        await asyncio.sleep(0.1)
        ipc.user_changes(mute=True)
        await asyncio.sleep(0.1)
        ipc.user_changes(mute=False)
        await asyncio.sleep(0.3)
        assert gx.status.doc["mixers"]["S1"]["cough_button"]["state"] == "MutedToX", "stream mute lost"
    finally:
        t.cancel()


async def test_two_way_skips_impossible_goxlr_state():
    # No trigger maps to a GoXLR state: pushing would be undone right away.
    eng, t = make(FakeIPC, Config(client_id="1", bidirectional=True, mute_triggers=["ToVoiceChat"]))
    try:
        ipc, gx = await connected(eng, status(S1=mixer(cough_type="ToStream")))
        await asyncio.sleep(0.05)
        ipc.user_changes(mute=True)
        await asyncio.sleep(0.2)
        assert gx.commands == []
        assert ipc.voice["mute"] is True  # the user's Discord mute is left alone
    finally:
        t.cancel()


async def test_unrelated_setting_change_keeps_discord_mute():
    eng, t = make(FakeIPC, Config(client_id="1"))
    try:
        ipc, _ = await connected(eng)
        await asyncio.sleep(0.05)
        ipc.user_changes(mute=True)
        eng.apply_config(Config(client_id="1", notifications=False))
        await asyncio.sleep(0.1)
        assert ipc.calls == [] and ipc.voice["mute"] is True
    finally:
        t.cancel()
