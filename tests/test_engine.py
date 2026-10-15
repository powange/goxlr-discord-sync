import asyncio

import pytest

from goxlr_discord_sync import auth
from goxlr_discord_sync.config import Config
from goxlr_discord_sync.discord_ipc import RPCError
from goxlr_discord_sync.engine import Engine
from goxlr_discord_sync.goxlr import GoXLRStatus

from .factories import mixer, status


class FakeIPC:
    """Behaves like the Discord client: deafen forces mute, undeafen restores it."""

    instances = []

    def __init__(self, client_id, on_event=None):
        self.client_id = client_id
        self.on_event = on_event
        self.voice = {"mute": False, "deaf": False}
        self._self_mute = False
        self.calls = []
        self.authorized = False
        self.closed = asyncio.Event()
        FakeIPC.instances.append(self)

    async def connect(self):
        return {}

    async def authorize(self, scopes, timeout=300):
        self.authorized = True
        return "code"

    async def authenticate(self, token):
        if token != "good":
            raise RPCError(4009, "Invalid access token")
        return {}

    async def get_voice_settings(self):
        return dict(self.voice)

    async def subscribe(self, evt, args=None):
        return {}

    async def set_voice_settings(self, **args):
        self.calls.append(args)
        if "deaf" in args:
            self.voice["deaf"] = args["deaf"]
            self.voice["mute"] = True if args["deaf"] else self._self_mute
        if "mute" in args:
            self._self_mute = args["mute"]
            self.voice["mute"] = args["mute"]
            if not args["mute"]:
                self.voice["deaf"] = False
        return dict(self.voice)

    def user_changes(self, **voice):
        """The user clicks in Discord."""
        self.voice.update(voice)
        self.on_event("VOICE_SETTINGS_UPDATE", dict(self.voice))

    async def wait_closed(self):
        await self.closed.wait()

    async def close(self):
        self.closed.set()

    @property
    def close_reason(self):
        return "closed"


class FakeGoXLR:
    instances = []

    def __init__(self, url, on_status):
        self.url = url
        self.on_status = on_status
        self.commands = []
        self.stop = asyncio.Event()
        self.status = None
        FakeGoXLR.instances.append(self)

    @property
    def connected(self):
        return self.status is not None

    async def push(self, doc):
        self.status = GoXLRStatus(doc)
        await self.on_status(self.status)

    async def patch(self, ops):
        self.status.apply_patch(ops)
        await self.on_status(self.status)

    async def run(self):
        await self.stop.wait()

    async def send_command(self, serial, command, timeout=5):
        self.commands.append((serial, command))
        key, value = next(iter(command.items()))
        if key == "SetCoughMuteState":
            await self.patch([{"op": "replace", "path": f"/mixers/{serial}/cough_button/state", "value": value}])
        else:
            fader, state = value
            await self.patch([{"op": "replace", "path": f"/mixers/{serial}/fader_status/{fader}/mute_state",
                               "value": state}])


class FakeTokens:
    def __init__(self, token="good"):
        self.token = token
        self.cleared = 0

    async def access_token(self, client_id, secret, force_refresh=False):
        return self.token

    async def exchange(self, client_id, secret, code, redirect_uris):
        self.token = "good"
        return "good"

    def clear(self):
        self.cleared += 1
        self.token = None


async def until(predicate, timeout=2):
    async def _wait():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(_wait(), timeout)


@pytest.fixture
def make_engine():
    FakeIPC.instances.clear()
    FakeGoXLR.instances.clear()
    tasks = []

    def _make(cfg=None, tokens=None, secret="s3cret", notes=None):
        cfg = cfg or Config(client_id="123")
        engine = Engine(cfg, token_manager=tokens or FakeTokens(), get_secret=lambda: secret,
                        ipc_factory=FakeIPC, goxlr_factory=FakeGoXLR,
                        notify=(lambda t, m: notes.append(t)) if notes is not None else None)
        tasks.append(asyncio.create_task(engine.run()))
        return engine

    yield _make
    for t in tasks:
        t.cancel()


async def connected(engine, doc=None):
    await until(lambda: FakeGoXLR.instances and FakeIPC.instances and engine.status.discord_connected)
    await FakeGoXLR.instances[-1].push(doc or status())
    return FakeIPC.instances[-1], FakeGoXLR.instances[-1]


async def test_cough_mutes_discord(make_engine):
    engine = make_engine()
    ipc, goxlr = await connected(engine)
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/cough_button/state", "value": "MutedToX"}])
    await until(lambda: ipc.voice["mute"] is True)
    assert ipc.calls == [{"mute": True}]
    assert engine.icon_spec() == ("muted", "mic", True)


async def test_mute_to_stream_does_not_mute_discord(make_engine):
    engine = make_engine()
    ipc, goxlr = await connected(engine, status(S1=mixer(cough_type="ToStream")))
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/cough_button/state", "value": "MutedToX"}])
    await asyncio.sleep(0.1)
    assert ipc.calls == []
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/cough_button/state", "value": "MutedToAll"}])
    await until(lambda: ipc.calls == [{"mute": True}])


async def test_chat_fader_deafens_and_restores(make_engine):
    engine = make_engine()
    ipc, goxlr = await connected(engine)
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/cough_button/state", "value": "MutedToAll"}])
    await until(lambda: ipc.voice["mute"] is True)
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/fader_status/B/mute_state", "value": "MutedToX"}])
    await until(lambda: ipc.voice["deaf"] is True)
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/fader_status/B/mute_state", "value": "Unmuted"}])
    await until(lambda: ipc.voice["deaf"] is False)
    await asyncio.sleep(0.05)
    # Never sent mute while deafened, mic still muted afterwards.
    assert ipc.calls == [{"mute": True}, {"deaf": True}, {"deaf": False}]
    assert ipc.voice["mute"] is True


async def test_unrelated_patch_does_not_override_discord(make_engine):
    engine = make_engine()
    ipc, goxlr = await connected(engine)
    await asyncio.sleep(0.05)
    ipc.user_changes(mute=True)  # muted by hand in Discord
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/levels/volumes/Chat", "value": 120}])
    await asyncio.sleep(0.1)
    assert ipc.calls == []


async def test_fader_reassignment_is_followed(make_engine):
    engine = make_engine()
    ipc, goxlr = await connected(engine)
    await goxlr.patch([
        {"op": "replace", "path": "/mixers/S1/fader_status/B/channel", "value": "Game"},
        {"op": "replace", "path": "/mixers/S1/fader_status/C/channel", "value": "Chat"},
    ])
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/fader_status/C/mute_state", "value": "MutedToAll"}])
    await until(lambda: ipc.voice["deaf"] is True)
    assert engine.status.deafen_fader == "C"


async def test_disabled_then_reenabled_resyncs(make_engine):
    engine = make_engine(Config(client_id="123", cough_sync=False))
    ipc, goxlr = await connected(engine)
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/cough_button/state", "value": "MutedToAll"}])
    await asyncio.sleep(0.1)
    assert ipc.calls == []
    engine.apply_config(Config(client_id="123", cough_sync=True))
    await until(lambda: ipc.calls == [{"mute": True}])


async def test_unknown_cough_state_is_not_pushed(make_engine):
    doc = status()
    del doc["mixers"]["S1"]["cough_button"]["state"]
    engine = make_engine()
    ipc, _ = await connected(engine, doc)
    ipc.user_changes(mute=True)
    await asyncio.sleep(0.1)
    assert ipc.calls == []


async def test_two_way_sync(make_engine):
    engine = make_engine(Config(client_id="123", bidirectional=True))
    ipc, goxlr = await connected(engine)
    await asyncio.sleep(0.05)
    ipc.user_changes(mute=True)
    await until(lambda: goxlr.commands == [("S1", {"SetCoughMuteState": "MutedToX"})])
    ipc.user_changes(deaf=True, mute=True)
    await until(lambda: ("S1", {"SetFaderMuteState": ["B", "MutedToX"]}) in goxlr.commands)
    await asyncio.sleep(0.1)
    assert ipc.calls == []  # nothing echoed back to Discord
    assert engine.status.goxlr_deaf is True


async def test_not_configured_waits_for_settings(make_engine):
    engine = make_engine(Config())
    await until(lambda: engine.status.auth == "not_configured")
    assert engine.summary() == "Setup required"
    assert engine.icon_spec()[0] == "idle"
    engine.apply_config(Config(client_id="123"))
    await until(lambda: engine.status.discord_connected)


async def test_first_run_authorizes_in_discord(make_engine):
    tokens = FakeTokens(token=None)
    engine = make_engine(tokens=tokens)
    await until(lambda: engine.status.discord_connected)
    assert FakeIPC.instances[-1].authorized


async def test_rejected_token_needs_new_authorization(make_engine, monkeypatch):
    monkeypatch.setattr("goxlr_discord_sync.engine.RETRY_MIN", 0.05)
    tokens = FakeTokens(token="revoked")
    engine = make_engine(tokens=tokens)
    # First session: authenticate fails twice, then the token is cleared and
    # the in-Discord prompt is shown automatically.
    await until(lambda: engine.status.discord_connected)
    assert tokens.cleared == 1
    assert FakeIPC.instances[-1].authorized


async def test_declined_authorization_waits_for_menu(make_engine, monkeypatch):
    async def deny(self, scopes, timeout=300):
        raise RPCError(5000, "OAuth2 Error: access_denied: The resource owner denied the request")

    monkeypatch.setattr(FakeIPC, "authorize", deny)
    engine = make_engine(tokens=FakeTokens(token=None))
    await until(lambda: engine.status.auth == "required")
    count = len(FakeIPC.instances)
    await asyncio.sleep(0.2)
    assert len(FakeIPC.instances) == count  # no retry loop
    monkeypatch.undo()
    engine.reauthorize()
    await until(lambda: engine.status.discord_connected)


async def test_transient_network_error_retries(make_engine, monkeypatch):
    class Flaky(FakeTokens):
        calls = 0

        async def access_token(self, *a, **k):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise auth.TransientAuthError("offline")
            return "good"

    monkeypatch.setattr("goxlr_discord_sync.engine.RETRY_MIN", 0.05)
    engine = make_engine(tokens=Flaky())
    await until(lambda: engine.status.discord_connected)
    assert Flaky.calls == 2


async def test_discord_restart_reconnects_and_notifies(make_engine, monkeypatch):
    monkeypatch.setattr("goxlr_discord_sync.engine.RETRY_MIN", 0.05)
    monkeypatch.setattr("goxlr_discord_sync.engine.LOSS_NOTIFY_DELAY", 0.05)
    notes = []
    engine = make_engine(notes=notes)
    ipc, goxlr = await connected(engine)
    await goxlr.patch([{"op": "replace", "path": "/mixers/S1/cough_button/state", "value": "MutedToAll"}])
    await until(lambda: ipc.voice["mute"] is True)
    await ipc.close()  # Discord quits
    await until(lambda: not engine.status.discord_connected)
    assert engine.icon_spec()[0] == "warning"
    await until(lambda: "Discord disconnected" in notes)
    await until(lambda: len(FakeIPC.instances) >= 2 and engine.status.discord_connected)
    # The new Discord session starts unmuted: state is pushed again right away.
    await until(lambda: FakeIPC.instances[-1].calls == [{"mute": True}])
    await until(lambda: "Discord reconnected" in notes)


async def test_goxlr_restart_is_handled(make_engine, monkeypatch):
    monkeypatch.setattr("goxlr_discord_sync.engine.RETRY_MIN", 0.05)
    engine = make_engine()
    ipc, goxlr = await connected(engine)
    goxlr.stop.set()
    await until(lambda: not engine.status.goxlr_connected)
    assert engine.summary() == "Waiting for GoXLR Utility"
    await until(lambda: len(FakeGoXLR.instances) >= 2)
    await FakeGoXLR.instances[-1].push(status(S1=mixer(cough="MutedToAll")))
    await until(lambda: ipc.voice["mute"] is True)


async def test_details_text(make_engine):
    engine = make_engine()
    await connected(engine)
    text = engine.details()
    assert "GoXLR Utility: connected (S1)" in text
    assert "Chat channel: fader B" in text


async def test_configured_goxlr_missing_pauses_sync(make_engine):
    engine = make_engine(Config(client_id="123", mixer_serial="S2"))
    ipc, goxlr = await connected(engine, status(S1=mixer(cough="MutedToAll")))
    await asyncio.sleep(0.05)
    assert ipc.calls == []  # S1 is not the configured device
    assert engine.status.missing_device == "S2"
    assert engine.summary() == "GoXLR S2 not connected"
    assert engine.icon_spec()[0] == "warning"
    assert "S2 is not plugged in" in engine.details()
    await goxlr.patch([{"op": "add", "path": "/mixers/S2", "value": mixer(cough="MutedToAll")}])
    await until(lambda: ipc.calls == [{"mute": True}])
    assert engine.status.missing_device is None
