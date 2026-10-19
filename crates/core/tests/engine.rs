//! Engine behaviour, ported from tests/test_engine.py and tests/test_engine_audit.py.

use async_trait::async_trait;
use gds_core::auth::{AuthError, TokenService};
use gds_core::config::Config;
use gds_core::discord::{DiscordConnection, DiscordConnector, EventSink, RpcError};
use gds_core::engine::{Auth, Deps, Engine, Hooks, IconColor, Timing};
use gds_core::goxlr::{GoxlrConnection, GoxlrConnector, StatusDoc, UpdateSink};
use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;
use std::rc::Rc;
use std::time::Duration;
use tokio::sync::watch;

// --- GoXLR status factories (tests/factories.py) ------------------------------

fn mixer(cough: &str, cough_type: &str) -> Value {
    json!({
        "hardware": {"device_type": "Full"},
        "cough_button": {"is_toggle": true, "mute_type": cough_type, "state": cough},
        "fader_status": {
            "A": {"channel": "Mic", "mute_type": "All", "mute_state": "Unmuted"},
            "B": {"channel": "Chat", "mute_type": "ToPhones", "mute_state": "Unmuted"},
            "C": {"channel": "Music", "mute_type": "All", "mute_state": "Unmuted"},
            "D": {"channel": "System", "mute_type": "All", "mute_state": "Unmuted"}},
        "levels": {"volumes": {"Chat": 200, "Music": 100}}
    })
}

fn status() -> Value {
    json!({"config": {"daemon_version": "1.2.3"}, "mixers": {"S1": mixer("Unmuted", "All")}})
}

fn status_with(m: Value) -> Value {
    json!({"mixers": {"S1": m}})
}

fn cough(state: &str) -> Value {
    json!([{"op": "replace", "path": "/mixers/S1/cough_button/state", "value": state}])
}

fn chat(state: &str) -> Value {
    json!([{"op": "replace", "path": "/mixers/S1/fader_status/B/mute_state", "value": state}])
}

// --- fake Discord ---------------------------------------------------------------

#[derive(Default)]
struct Behaviour {
    deny_authorize: Cell<bool>,
    /// Send VOICE_SETTINGS_UPDATE, then answer SET_VOICE_SETTINGS after this delay.
    event_first: Cell<Option<Duration>>,
    /// Answer SET_VOICE_SETTINGS after (d1 + d2) with the event sent after d1.
    event_between: Cell<Option<(Duration, Duration)>>,
    fail_next_set: Cell<bool>,
}

/// Behaves like the Discord client: deafen forces mute, undeafen restores it.
struct FakeIpc {
    voice: RefCell<Value>,
    self_mute: Cell<bool>,
    calls: RefCell<Vec<Value>>,
    authorized: Cell<bool>,
    channel: RefCell<Option<Value>>,
    closed: watch::Sender<Option<String>>,
    sink: EventSink,
    behaviour: Rc<Behaviour>,
}

impl FakeIpc {
    fn voice(&self, field: &str) -> bool {
        self.voice.borrow()[field] == true
    }
    /// The user clicks in Discord.
    fn user_changes(&self, changes: Value) {
        for (k, v) in changes.as_object().unwrap() {
            self.voice.borrow_mut()[k] = v.clone();
        }
        let voice = self.voice.borrow().clone();
        (self.sink)("VOICE_SETTINGS_UPDATE", voice);
    }
    fn user_joins(&self, name: &str) {
        *self.channel.borrow_mut() = Some(json!({"id": "c1", "name": name, "guild_id": "g1"}));
        (self.sink)("VOICE_CHANNEL_SELECT", json!({"channel_id": "c1", "guild_id": "g1"}));
    }
    fn calls(&self) -> Vec<Value> {
        self.calls.borrow().clone()
    }
    fn apply(&self, args: &Value) {
        let mut voice = self.voice.borrow_mut();
        if let Some(deaf) = args["deaf"].as_bool() {
            voice["deaf"] = json!(deaf);
            voice["mute"] = json!(deaf || self.self_mute.get());
        }
        if let Some(mute) = args["mute"].as_bool() {
            self.self_mute.set(mute);
            voice["mute"] = json!(mute);
            if !mute {
                voice["deaf"] = json!(false);
            }
        }
    }
}

#[async_trait(?Send)]
impl DiscordConnection for FakeIpc {
    async fn command(&self, cmd: &str, args: Value, _evt: Option<&str>, _timeout: Duration) -> Result<Value, RpcError> {
        if let Some(reason) = self.closed.borrow().clone() {
            return Err(RpcError::Closed(reason));
        }
        match cmd {
            "AUTHORIZE" => {
                self.authorized.set(true);
                if self.behaviour.deny_authorize.get() {
                    return Err(RpcError::Rpc { code: Some(5000),
                        message: "OAuth2 Error: access_denied: The resource owner denied the request".into() });
                }
                Ok(json!({"code": "code"}))
            }
            "AUTHENTICATE" if args["access_token"] != "good" => {
                Err(RpcError::Rpc { code: Some(4009), message: "Invalid access token".into() })
            }
            "GET_VOICE_SETTINGS" => Ok(self.voice.borrow().clone()),
            "GET_SELECTED_VOICE_CHANNEL" => Ok(self.channel.borrow().clone().unwrap_or(json!({}))),
            "GET_GUILD" => Ok(json!({"id": "g1", "name": "My Server"})),
            "SELECT_VOICE_CHANNEL" => {
                self.calls.borrow_mut().push(json!({"select_voice_channel": args["channel_id"]}));
                *self.channel.borrow_mut() = None;
                (self.sink)("VOICE_CHANNEL_SELECT", json!({"channel_id": null}));
                Ok(json!({}))
            }
            "SET_VOICE_SETTINGS" => {
                if self.behaviour.fail_next_set.replace(false) {
                    return Err(RpcError::Rpc { code: Some(1000), message: "transient".into() });
                }
                self.calls.borrow_mut().push(args.clone());
                self.apply(&args);
                if let Some(delay) = self.behaviour.event_first.get() {
                    let voice = self.voice.borrow().clone();
                    (self.sink)("VOICE_SETTINGS_UPDATE", voice);
                    tokio::time::sleep(delay).await;
                } else if let Some((d1, d2)) = self.behaviour.event_between.get() {
                    let snapshot = self.voice.borrow().clone();
                    tokio::time::sleep(d1).await;
                    (self.sink)("VOICE_SETTINGS_UPDATE", snapshot);
                    tokio::time::sleep(d2).await;
                }
                Ok(self.voice.borrow().clone())
            }
            _ => Ok(json!({})),
        }
    }

    async fn wait_closed(&self) -> String {
        let mut rx = self.closed.subscribe();
        let reason = rx.wait_for(|r| r.is_some()).await.map(|r| r.clone().unwrap()).unwrap_or_default();
        reason
    }

    fn close(&self) {
        self.closed.send_replace(Some("closed".into()));
    }
}

#[derive(Default)]
struct FakeDiscord {
    instances: RefCell<Vec<Rc<FakeIpc>>>,
    behaviour: Rc<Behaviour>,
}

impl FakeDiscord {
    fn last(&self) -> Rc<FakeIpc> {
        self.instances.borrow().last().unwrap().clone()
    }
    fn count(&self) -> usize {
        self.instances.borrow().len()
    }
}

#[async_trait(?Send)]
impl DiscordConnector for FakeDiscord {
    async fn connect(&self, _client_id: &str, events: EventSink) -> Result<Rc<dyn DiscordConnection>, RpcError> {
        let ipc = Rc::new(FakeIpc {
            voice: RefCell::new(json!({"mute": false, "deaf": false})),
            self_mute: Cell::new(false),
            calls: RefCell::default(),
            authorized: Cell::new(false),
            channel: RefCell::default(),
            closed: watch::channel(None).0,
            sink: events,
            behaviour: self.behaviour.clone(),
        });
        self.instances.borrow_mut().push(ipc.clone());
        Ok(ipc)
    }
}

// --- fake GoXLR Utility ---------------------------------------------------------

struct FakeGoxlr {
    status: RefCell<Option<StatusDoc>>,
    sink: UpdateSink,
    commands: RefCell<Vec<(String, Value)>>,
    stop: watch::Sender<bool>,
}

impl FakeGoxlr {
    fn push(&self, doc: Value) {
        *self.status.borrow_mut() = Some(StatusDoc::new(doc));
        (self.sink)();
    }
    fn patch(&self, ops: Value) {
        self.status.borrow_mut().as_mut().unwrap().apply_patch(&ops).unwrap();
        (self.sink)();
    }
    fn field(&self, path: &str) -> Value {
        self.status.borrow().as_ref().unwrap().doc.pointer(path).cloned().unwrap_or(Value::Null)
    }
    fn commands(&self) -> Vec<(String, Value)> {
        self.commands.borrow().clone()
    }
}

#[async_trait(?Send)]
impl GoxlrConnection for FakeGoxlr {
    async fn run(&self) -> Result<(), String> {
        let _ = self.stop.subscribe().wait_for(|s| *s).await;
        Err("stopped".into())
    }
    async fn send_command(&self, serial: &str, command: Value) -> Result<(), String> {
        self.commands.borrow_mut().push((serial.to_string(), command.clone()));
        let (key, value) = command.as_object().unwrap().iter().next().unwrap();
        let ops = if key == "SetCoughMuteState" {
            json!([{"op": "replace", "path": format!("/mixers/{serial}/cough_button/state"), "value": value}])
        } else {
            json!([{"op": "replace", "path": format!("/mixers/{serial}/fader_status/{}/mute_state", value[0].as_str().unwrap()), "value": value[1]}])
        };
        self.patch(ops);
        Ok(())
    }
    fn connected(&self) -> bool {
        self.status.borrow().is_some()
    }
    fn status(&self) -> Option<StatusDoc> {
        self.status.borrow().clone()
    }
}

#[derive(Default)]
struct FakeGoxlrs(RefCell<Vec<Rc<FakeGoxlr>>>);

impl FakeGoxlrs {
    fn last(&self) -> Rc<FakeGoxlr> {
        self.0.borrow().last().unwrap().clone()
    }
    fn count(&self) -> usize {
        self.0.borrow().len()
    }
}

impl GoxlrConnector for FakeGoxlrs {
    fn create(&self, _url: &str, updates: UpdateSink) -> Rc<dyn GoxlrConnection> {
        let conn = Rc::new(FakeGoxlr { status: RefCell::default(), sink: updates, commands: RefCell::default(),
                                       stop: watch::channel(false).0 });
        self.0.borrow_mut().push(conn.clone());
        conn
    }
}

// --- fake tokens ------------------------------------------------------------------

struct FakeTokens {
    token: RefCell<Option<String>>,
    cleared: Cell<u32>,
    transient_errors: Cell<u32>,
    calls: Cell<u32>,
}

impl FakeTokens {
    fn new(token: Option<&str>) -> Rc<FakeTokens> {
        Rc::new(FakeTokens { token: RefCell::new(token.map(str::to_string)), cleared: Cell::new(0),
                             transient_errors: Cell::new(0), calls: Cell::new(0) })
    }
}

#[async_trait(?Send)]
impl TokenService for FakeTokens {
    async fn access_token(&self, _: &str, _: &str, _: bool) -> Result<Option<String>, AuthError> {
        self.calls.set(self.calls.get() + 1);
        if self.transient_errors.get() > 0 {
            self.transient_errors.set(self.transient_errors.get() - 1);
            return Err(AuthError::Transient("offline".into()));
        }
        Ok(self.token.borrow().clone())
    }
    async fn exchange(&self, _: &str, _: &str, _: &str, _: &[Option<String>]) -> Result<String, AuthError> {
        *self.token.borrow_mut() = Some("good".into());
        Ok("good".into())
    }
    fn clear(&self) {
        self.cleared.set(self.cleared.get() + 1);
        *self.token.borrow_mut() = None;
    }
    async fn browser_authorize(&self, _: &str, _: &str, _: u16, _: Duration, _: watch::Receiver<bool>)
        -> Result<String, AuthError> {
        Err(AuthError::Refused("no browser in tests".into()))
    }
}

// --- harness ------------------------------------------------------------------------

struct World {
    engine: Engine,
    discord: Rc<FakeDiscord>,
    goxlrs: Rc<FakeGoxlrs>,
    notes: Rc<RefCell<Vec<String>>>,
}

fn cfg() -> Config {
    Config { client_id: "123".into(), ..Config::default() }
}

fn world_with(config: Config, tokens: Rc<FakeTokens>, secret: &str) -> World {
    let discord = Rc::new(FakeDiscord::default());
    let goxlrs = Rc::new(FakeGoxlrs::default());
    let notes: Rc<RefCell<Vec<String>>> = Rc::default();
    let sink = notes.clone();
    let secret = secret.to_string();
    let engine = Engine::new(config, Deps {
        tokens: tokens.clone(),
        get_secret: Box::new(move || secret.clone()),
        discord: discord.clone(),
        goxlr: goxlrs.clone(),
        hooks: Hooks { on_change: Box::new(|| {}), notify: Box::new(move |title, _| sink.borrow_mut().push(title.to_string())) },
        timing: Timing {
            retry_min: Duration::from_millis(50),
            retry_max: Duration::from_millis(400),
            // Shorter than retry_min: a loss is notified before the reconnection (as the Python test relied on).
            loss_notify_delay: Duration::from_millis(20),
            browser_timeout: Duration::from_secs(1),
        },
    });
    let runner = engine.clone();
    tokio::task::spawn_local(async move { runner.run().await });
    World { engine, discord, goxlrs, notes }
}

fn world(config: Config) -> World {
    world_with(config, FakeTokens::new(Some("good")), "s3cret")
}

async fn until(what: &str, condition: impl Fn() -> bool) {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(3);
    while !condition() {
        assert!(tokio::time::Instant::now() < deadline, "timed out waiting for {what}");
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}

async fn sleep_ms(ms: u64) {
    tokio::time::sleep(Duration::from_millis(ms)).await;
}

async fn connected(w: &World, doc: Value) -> (Rc<FakeIpc>, Rc<FakeGoxlr>) {
    until("both connections", || w.goxlrs.count() > 0 && w.discord.count() > 0 && w.engine.status().discord_connected).await;
    let goxlr = w.goxlrs.last();
    goxlr.push(doc);
    (w.discord.last(), goxlr)
}

macro_rules! local_test {
    ($name:ident, $body:expr) => {
        #[tokio::test(flavor = "current_thread")]
        async fn $name() {
            tokio::task::LocalSet::new().run_until($body).await;
        }
    };
}

// --- tests/test_engine.py --------------------------------------------------------

local_test!(cough_mutes_discord, async {
    let w = world(cfg());
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.patch(cough("MutedToX"));
    until("mute", || ipc.voice("mute")).await;
    assert_eq!(ipc.calls(), vec![json!({"mute": true})]);
    let icon = w.engine.icon_spec();
    assert_eq!((icon.color, icon.slashed), (IconColor::Muted, true));
});

local_test!(mute_to_stream_does_not_mute_discord, async {
    let w = world(cfg());
    let (ipc, goxlr) = connected(&w, status_with(mixer("Unmuted", "ToStream"))).await;
    goxlr.patch(cough("MutedToX"));
    sleep_ms(100).await;
    assert!(ipc.calls().is_empty());
    goxlr.patch(cough("MutedToAll"));
    until("mute", || ipc.calls() == vec![json!({"mute": true})]).await;
});

local_test!(chat_fader_deafens_and_restores, async {
    let w = world(cfg());
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.patch(cough("MutedToAll"));
    until("mute", || ipc.voice("mute")).await;
    goxlr.patch(chat("MutedToX"));
    until("deaf", || ipc.voice("deaf")).await;
    goxlr.patch(chat("Unmuted"));
    until("undeaf", || !ipc.voice("deaf")).await;
    sleep_ms(50).await;
    // Never sent mute while deafened, mic still muted afterwards.
    assert_eq!(ipc.calls(), vec![json!({"mute": true}), json!({"deaf": true}), json!({"deaf": false})]);
    assert!(ipc.voice("mute"));
});

local_test!(unrelated_patch_does_not_override_discord, async {
    let w = world(cfg());
    let (ipc, goxlr) = connected(&w, status()).await;
    sleep_ms(50).await;
    ipc.user_changes(json!({"mute": true})); // muted by hand in Discord
    goxlr.patch(json!([{"op": "replace", "path": "/mixers/S1/levels/volumes/Chat", "value": 120}]));
    sleep_ms(100).await;
    assert!(ipc.calls().is_empty());
});

local_test!(fader_reassignment_is_followed, async {
    let w = world(cfg());
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.patch(json!([
        {"op": "replace", "path": "/mixers/S1/fader_status/B/channel", "value": "Game"},
        {"op": "replace", "path": "/mixers/S1/fader_status/C/channel", "value": "Chat"}]));
    goxlr.patch(json!([{"op": "replace", "path": "/mixers/S1/fader_status/C/mute_state", "value": "MutedToAll"}]));
    until("deaf", || ipc.voice("deaf")).await;
    assert_eq!(w.engine.status().deafen_fader.as_deref(), Some("C"));
});

local_test!(disabled_then_reenabled_resyncs, async {
    let w = world(Config { cough_sync: false, ..cfg() });
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.patch(cough("MutedToAll"));
    sleep_ms(100).await;
    assert!(ipc.calls().is_empty());
    w.engine.apply_config(cfg(), false);
    until("resync", || ipc.calls() == vec![json!({"mute": true})]).await;
});

local_test!(unknown_cough_state_is_not_pushed, async {
    let w = world(cfg());
    let mut doc = status();
    doc["mixers"]["S1"]["cough_button"].as_object_mut().unwrap().remove("state");
    let (ipc, _) = connected(&w, doc).await;
    ipc.user_changes(json!({"mute": true}));
    sleep_ms(100).await;
    assert!(ipc.calls().is_empty());
});

local_test!(two_way_sync, async {
    let w = world(Config { bidirectional: true, ..cfg() });
    let (ipc, goxlr) = connected(&w, status()).await;
    sleep_ms(50).await;
    ipc.user_changes(json!({"mute": true}));
    until("cough command", || goxlr.commands() == vec![("S1".into(), json!({"SetCoughMuteState": "MutedToX"}))]).await;
    ipc.user_changes(json!({"deaf": true, "mute": true}));
    until("fader command", || goxlr.commands().contains(&("S1".into(), json!({"SetFaderMuteState": ["B", "MutedToX"]})))).await;
    sleep_ms(100).await;
    assert!(ipc.calls().is_empty()); // nothing echoed back to Discord
    assert_eq!(w.engine.status().goxlr_deaf, Some(true));
});

local_test!(not_configured_waits_for_settings, async {
    let w = world(Config::default());
    until("not configured", || w.engine.status().auth == Auth::NotConfigured).await;
    assert_eq!(w.engine.summary(), "Setup required");
    assert_eq!(w.engine.icon_spec().color, IconColor::Idle);
    w.engine.apply_config(cfg(), false);
    until("connected", || w.engine.status().discord_connected).await;
});

local_test!(first_run_authorizes_in_discord, async {
    let w = world_with(cfg(), FakeTokens::new(None), "s3cret");
    until("connected", || w.engine.status().discord_connected).await;
    assert!(w.discord.last().authorized.get());
});

local_test!(rejected_token_needs_new_authorization, async {
    let tokens = FakeTokens::new(Some("revoked"));
    let w = world_with(cfg(), tokens.clone(), "s3cret");
    // First sessions: authenticate fails twice, then the token is cleared and the
    // in-Discord prompt is shown automatically.
    until("connected", || w.engine.status().discord_connected).await;
    assert_eq!(tokens.cleared.get(), 1);
    assert!(w.discord.last().authorized.get());
});

local_test!(declined_authorization_waits_for_menu, async {
    let w = world_with(cfg(), FakeTokens::new(None), "s3cret");
    w.discord.behaviour.deny_authorize.set(true);
    until("required", || w.engine.status().auth == Auth::Required).await;
    let count = w.discord.count();
    sleep_ms(200).await;
    assert_eq!(w.discord.count(), count); // no retry loop
    w.discord.behaviour.deny_authorize.set(false);
    w.engine.reauthorize();
    until("connected", || w.engine.status().discord_connected).await;
});

local_test!(transient_network_error_retries, async {
    let tokens = FakeTokens::new(Some("good"));
    tokens.transient_errors.set(1);
    let w = world_with(cfg(), tokens.clone(), "s3cret");
    until("connected", || w.engine.status().discord_connected).await;
    assert_eq!(tokens.calls.get(), 2);
});

local_test!(discord_restart_reconnects_and_notifies, async {
    let w = world(cfg());
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.patch(cough("MutedToAll"));
    until("mute", || ipc.voice("mute")).await;
    ipc.close(); // Discord quits
    until("disconnected", || !w.engine.status().discord_connected).await;
    assert_eq!(w.engine.icon_spec().color, IconColor::Warning);
    until("loss notice", || w.notes.borrow().contains(&"Discord disconnected".to_string())).await;
    until("reconnected", || w.discord.count() >= 2 && w.engine.status().discord_connected).await;
    // The new Discord session starts unmuted: state is pushed again right away.
    until("pushed again", || w.discord.last().calls() == vec![json!({"mute": true})]).await;
    until("restore notice", || w.notes.borrow().contains(&"Discord reconnected".to_string())).await;
});

local_test!(goxlr_restart_is_handled, async {
    let w = world(cfg());
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.stop.send_replace(true);
    until("goxlr down", || !w.engine.status().goxlr_connected).await;
    assert_eq!(w.engine.summary(), "Waiting for GoXLR Utility");
    until("new session", || w.goxlrs.count() >= 2).await;
    w.goxlrs.last().push(status_with(mixer("MutedToAll", "All")));
    until("mute", || ipc.voice("mute")).await;
});

local_test!(details_text, async {
    let w = world(cfg());
    connected(&w, status()).await;
    until("goxlr", || w.engine.status().goxlr_connected).await;
    let text = w.engine.details();
    assert!(text.contains("GoXLR Utility: connected (S1)"), "{text}");
    assert!(text.contains("Chat channel: fader B"), "{text}");
});

local_test!(configured_goxlr_missing_pauses_sync, async {
    let w = world(Config { mixer_serial: "S2".into(), ..cfg() });
    let (ipc, goxlr) = connected(&w, status_with(mixer("MutedToAll", "All"))).await;
    sleep_ms(50).await;
    assert!(ipc.calls().is_empty()); // S1 is not the configured device
    assert_eq!(w.engine.status().missing_device.as_deref(), Some("S2"));
    assert_eq!(w.engine.summary(), "GoXLR S2 not connected");
    assert_eq!(w.engine.icon_spec().color, IconColor::Warning);
    assert!(w.engine.details().contains("S2 is not plugged in"));
    goxlr.patch(json!([{"op": "add", "path": "/mixers/S2", "value": mixer("MutedToAll", "All")}]));
    until("mute", || ipc.calls() == vec![json!({"mute": true})]).await;
    assert_eq!(w.engine.status().missing_device, None);
});

local_test!(voice_channel_in_tooltip_and_status, async {
    let w = world(cfg());
    let (ipc, _) = connected(&w, status()).await;
    assert_eq!(w.engine.status().voice_channel, None);
    ipc.user_joins("General");
    until("channel", || w.engine.status().voice_channel.as_deref() == Some("My Server › General")).await;
    until("goxlr", || w.engine.status().goxlr_connected).await;
    assert!(w.engine.summary().ends_with(" - My Server › General"));
    assert!(w.engine.details().contains("Voice channel: My Server › General"));
});

local_test!(goxlr_buttons_trigger_discord_actions, async {
    let mut actions = BTreeMap::new();
    actions.insert("toggle_deafen".to_string(), "SamplerClear".to_string());
    actions.insert("leave_voice".to_string(), "Bleep".to_string());
    let w = world(Config { button_actions: actions, ..cfg() });
    let mut doc = status();
    doc["mixers"]["S1"]["button_down"] = json!({"SamplerClear": false, "Bleep": false});
    let (ipc, goxlr) = connected(&w, doc).await;
    ipc.user_joins("General");
    until("channel", || w.engine.status().voice_channel.is_some()).await;
    let press = |button: &str, down: bool| {
        goxlr.patch(json!([{"op": "replace", "path": format!("/mixers/S1/button_down/{button}"), "value": down}]));
    };
    press("SamplerClear", true);
    until("deaf", || ipc.voice("deaf")).await;
    press("SamplerClear", false); // release: nothing
    press("SamplerClear", true);
    until("undeaf", || !ipc.voice("deaf")).await;
    press("Bleep", true);
    until("leave", || ipc.calls().contains(&json!({"select_voice_channel": null}))).await;
    until("no channel", || w.engine.status().voice_channel.is_none()).await;
});

local_test!(button_action_is_mirrored_with_two_way_sync, async {
    let mut actions = BTreeMap::new();
    actions.insert("toggle_mute".to_string(), "EffectFx".to_string());
    let w = world(Config { bidirectional: true, button_actions: actions, ..cfg() });
    let mut doc = status();
    doc["mixers"]["S1"]["button_down"] = json!({"EffectFx": false});
    let (ipc, goxlr) = connected(&w, doc).await;
    goxlr.patch(json!([{"op": "replace", "path": "/mixers/S1/button_down/EffectFx", "value": true}]));
    until("cough command", || goxlr.commands().contains(&("S1".into(), json!({"SetCoughMuteState": "MutedToX"})))).await;
    assert!(ipc.voice("mute"));
});

// --- tests/test_engine_audit.py (hot mic, echo handling, retries) ------------------

fn cough_state(goxlr: &FakeGoxlr) -> Value {
    goxlr.field("/mixers/S1/cough_button/state")
}

local_test!(hot_mic_after_undeafen_in_discord_two_way, async {
    let w = world(Config { bidirectional: true, ..cfg() });
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.patch(chat("MutedToX")); // deafen via Chat fader
    until("deaf", || ipc.voice("deaf")).await;
    goxlr.patch(cough("MutedToX")); // the user mutes the mic on the GoXLR while deafened
    sleep_ms(100).await;
    ipc.user_changes(json!({"deaf": false, "mute": false})); // undeafen in Discord; Discord restores mute=false
    sleep_ms(200).await;
    assert_eq!(cough_state(&goxlr), "MutedToX", "cough mute was undone: hot mic");
});

local_test!(hot_mic_after_undeafen_in_discord_one_way, async {
    let w = world(cfg());
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.patch(chat("MutedToX"));
    until("deaf", || ipc.voice("deaf")).await;
    goxlr.patch(cough("MutedToX"));
    sleep_ms(100).await;
    ipc.user_changes(json!({"deaf": false, "mute": false}));
    sleep_ms(200).await;
    assert!(ipc.voice("mute"), "GoXLR cough is muted but Discord mic is live");
});

local_test!(event_before_response_undeafen_unmutes_goxlr, async {
    let w = world(Config { bidirectional: true, ..cfg() });
    w.discord.behaviour.event_first.set(Some(Duration::from_millis(50)));
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.patch(chat("MutedToX"));
    until("deaf", || ipc.voice("deaf")).await;
    sleep_ms(100).await;
    goxlr.patch(cough("MutedToX"));
    sleep_ms(100).await;
    goxlr.patch(chat("Unmuted"));
    sleep_ms(500).await;
    assert_eq!(cough_state(&goxlr), "MutedToX");
});

local_test!(event_before_response_rapid_toggle, async {
    let w = world(Config { bidirectional: true, ..cfg() });
    w.discord.behaviour.event_first.set(Some(Duration::from_millis(50)));
    let (ipc, goxlr) = connected(&w, status()).await;
    sleep_ms(100).await;
    goxlr.patch(cough("MutedToX"));
    sleep_ms(10).await; // set mute=true in flight
    goxlr.patch(cough("Unmuted")); // the user toggles back quickly
    sleep_ms(600).await;
    assert_eq!(cough_state(&goxlr), "Unmuted");
    assert!(!ipc.voice("mute"));
});

local_test!(failed_set_is_retried, async {
    let w = world(cfg());
    let (ipc, goxlr) = connected(&w, status()).await;
    w.discord.behaviour.fail_next_set.set(true);
    goxlr.patch(cough("MutedToX"));
    sleep_ms(300).await;
    assert!(ipc.voice("mute"), "GoXLR muted, Discord stays live, no retry");
});

local_test!(user_change_right_after_a_sync_is_kept, async {
    let w = world(Config { bidirectional: true, ..cfg() });
    let (ipc, goxlr) = connected(&w, status()).await;
    goxlr.patch(cough("MutedToX"));
    until("mute", || ipc.voice("mute")).await;
    sleep_ms(300).await;
    ipc.user_changes(json!({"mute": false})); // the user clicks unmute in Discord 0.3 s later
    sleep_ms(300).await;
    assert_eq!(cough_state(&goxlr), "Unmuted");
});

local_test!(rapid_toggle_event_between, async {
    let w = world(Config { bidirectional: true, ..cfg() });
    w.discord.behaviour.event_between.set(Some((Duration::from_millis(30), Duration::from_millis(20))));
    let (_ipc, goxlr) = connected(&w, status()).await;
    sleep_ms(100).await;
    goxlr.patch(cough("MutedToX"));
    sleep_ms(10).await;
    goxlr.patch(cough("Unmuted"));
    sleep_ms(500).await;
    assert_eq!(cough_state(&goxlr), "Unmuted");
});

local_test!(rapid_toggle_event_between_hot_mic, async {
    let w = world(Config { bidirectional: true, ..cfg() });
    w.discord.behaviour.event_between.set(Some((Duration::from_millis(30), Duration::from_millis(20))));
    let (ipc, goxlr) = connected(&w, status_with(mixer("MutedToX", "All"))).await;
    until("mute", || ipc.voice("mute")).await;
    sleep_ms(100).await;
    goxlr.patch(cough("Unmuted"));
    sleep_ms(10).await;
    goxlr.patch(cough("MutedToX")); // the user re-mutes immediately
    sleep_ms(500).await;
    assert!(ipc.voice("mute"));
});

local_test!(two_way_discards_stream_mute, async {
    let w = world(Config { bidirectional: true, ..cfg() });
    let (ipc, goxlr) = connected(&w, status_with(mixer("MutedToX", "ToStream"))).await;
    sleep_ms(100).await;
    ipc.user_changes(json!({"mute": true}));
    sleep_ms(100).await;
    ipc.user_changes(json!({"mute": false}));
    sleep_ms(300).await;
    assert_eq!(cough_state(&goxlr), "MutedToX", "stream mute lost");
});

local_test!(two_way_skips_impossible_goxlr_state, async {
    // No trigger maps to a GoXLR state: pushing would be undone right away.
    let w = world(Config { bidirectional: true, mute_triggers: vec!["ToVoiceChat".into()], ..cfg() });
    let (ipc, goxlr) = connected(&w, status_with(mixer("Unmuted", "ToStream"))).await;
    sleep_ms(50).await;
    ipc.user_changes(json!({"mute": true}));
    sleep_ms(200).await;
    assert!(goxlr.commands().is_empty());
    assert!(ipc.voice("mute")); // the user's Discord mute is left alone
});

local_test!(unrelated_setting_change_keeps_discord_mute, async {
    let w = world(cfg());
    let (ipc, _) = connected(&w, status()).await;
    sleep_ms(50).await;
    ipc.user_changes(json!({"mute": true}));
    w.engine.apply_config(Config { notifications: false, ..cfg() }, false);
    sleep_ms(100).await;
    assert!(ipc.calls().is_empty() && ipc.voice("mute"));
});
