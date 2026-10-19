//! Synchronisation engine between GoXLR Utility and the Discord client.
//!
//! Three loops run side by side on one thread (a tokio LocalSet), like the
//! Python asyncio engine they replace:
//!
//! * the GoXLR loop keeps a websocket session with GoXLR Utility and derives
//!   the wanted Discord mute/deafen state from the device status;
//! * the Discord loop keeps the RPC connection (with authorization) and tracks
//!   Discord's actual voice settings through VOICE_SETTINGS_UPDATE events;
//! * the reconcile loop pushes the wanted state to Discord whenever it changes.
//!
//! A lost connection on one side never blocks the other side.

use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::rc::Rc;
use std::time::{Duration, Instant};
use tokio::sync::{watch, Notify};
use tokio::task::{spawn_local, JoinHandle};

use crate::auth::{AuthError, TokenService, SCOPES};
use crate::config::{Config, BUTTON_ACTIONS};
use crate::discord::{DiscordConnection, DiscordConnector, EventSink, Rpc, RpcError};
use crate::goxlr::{GoxlrConnection, GoxlrConnector, MixerSnapshot, StatusDoc};
use crate::i18n::tr;
use crate::sync::{desired_flag, goxlr_state_for, plan_discord_update, VoiceChange};

const GOXLR: &str = "GoXLR Utility";
const DISCORD: &str = "Discord";
const MUTE: &str = "mute";
const DEAF: &str = "deaf";

#[derive(Clone, Debug)]
pub struct Timing {
    pub retry_min: Duration,
    pub retry_max: Duration,
    /// How long a connection must stay down before notifying.
    pub loss_notify_delay: Duration,
    pub browser_timeout: Duration,
}

impl Default for Timing {
    fn default() -> Self {
        Timing {
            retry_min: Duration::from_secs(2),
            retry_max: Duration::from_secs(30),
            loss_notify_delay: Duration::from_secs(10),
            browser_timeout: Duration::from_secs(180),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Auth {
    #[default]
    Unknown,
    NotConfigured,
    Authorizing,
    Required,
    Ok,
}

#[derive(Clone, Debug, PartialEq, Default)]
pub struct Status {
    pub goxlr_connected: bool,
    pub discord_connected: bool,
    pub auth: Auth,
    /// Wanted by the GoXLR.
    pub goxlr_mute: Option<bool>,
    pub goxlr_deaf: Option<bool>,
    /// Actually set in Discord.
    pub discord_mute: Option<bool>,
    pub discord_deaf: Option<bool>,
    pub serial: Option<String>,
    pub deafen_fader: Option<String>,
    /// Configured GoXLR serial that is not connected.
    pub missing_device: Option<String>,
    /// Discord voice channel the user is in.
    pub voice_channel: Option<String>,
    pub detail: String,
    pub last_latency_ms: Option<f64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IconColor {
    /// Connected, mic open.
    Live,
    /// Connected, mic muted or deafened.
    Muted,
    /// GoXLR Utility or Discord unreachable.
    Warning,
    /// Setup or authorization required.
    Idle,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Glyph {
    Mic,
    Headphones,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct IconSpec {
    pub color: IconColor,
    pub glyph: Glyph,
    pub slashed: bool,
}

/// Show a notification (title, message).
pub type Notifier = Box<dyn Fn(&str, &str)>;

pub struct Hooks {
    /// The status shown to the user changed.
    pub on_change: Box<dyn Fn()>,
    pub notify: Notifier,
}

impl Default for Hooks {
    fn default() -> Self {
        Hooks {
            on_change: Box::new(|| {}),
            notify: Box::new(|_, _| {}),
        }
    }
}

/// Equivalent of asyncio.Event: stays set until cleared.
#[derive(Default)]
struct Flag {
    set: Cell<bool>,
    notify: Notify,
}

impl Flag {
    fn set(&self) {
        self.set.set(true);
        self.notify.notify_waiters();
    }
    fn clear(&self) {
        self.set.set(false);
    }
    fn is_set(&self) -> bool {
        self.set.get()
    }
    async fn wait(&self) {
        loop {
            let notified = self.notify.notified(); // registered before the check: no lost wake-up
            if self.set.get() {
                return;
            }
            notified.await;
        }
    }
}

/// What the user sees: the status and the two sync switches.
type Signature = (Status, bool, bool);

/// Why a Discord session ended.
enum End {
    /// Nothing more can happen until the user acts (setup or authorization).
    NeedsUser(String),
    Failed(String),
}

pub struct Deps {
    pub tokens: Rc<dyn TokenService>,
    pub get_secret: Box<dyn Fn() -> String>,
    pub discord: Rc<dyn DiscordConnector>,
    pub goxlr: Rc<dyn GoxlrConnector>,
    pub hooks: Hooks,
    pub timing: Timing,
}

struct Inner {
    cfg: RefCell<Config>,
    status: RefCell<Status>,
    deps: Deps,
    ipc: RefCell<Option<Rc<dyn DiscordConnection>>>,
    goxlr_conn: RefCell<Option<Rc<dyn GoxlrConnection>>>,
    goxlr_status: RefCell<Option<StatusDoc>>,
    snapshot: RefCell<Option<MixerSnapshot>>,
    may_prompt: Cell<bool>,
    force_refresh: Cell<bool>,
    auth_failures: Cell<u32>,
    /// A SET_VOICE_SETTINGS is waiting for its response.
    set_in_flight: Cell<bool>,
    /// A button action is waiting for its response.
    action_in_flight: Cell<bool>,
    events_during_set: Cell<bool>,
    set_failures: Cell<u32>,
    retry: RefCell<Option<JoinHandle<()>>>,
    reconcile_fields: RefCell<BTreeSet<&'static str>>,
    reconcile_needed: Flag,
    discord_wakeup: Flag,
    goxlr_restart: Flag,
    ever_connected: RefCell<HashSet<&'static str>>,
    loss_timers: RefCell<HashMap<&'static str, JoinHandle<()>>>,
    loss_notified: RefCell<HashSet<&'static str>>,
    /// GoXLR state written by two-way sync: key -> (state we set, state to restore on unmute).
    pushed: RefCell<HashMap<&'static str, (String, String)>>,
    push_lock: tokio::sync::Mutex<()>,
    cancel_browser: watch::Sender<bool>,
    last_signature: RefCell<Option<Signature>>,
    tasks: RefCell<Vec<JoinHandle<()>>>,
}

/// Cheap handle (Rc) to the engine; every method must run inside the engine's LocalSet.
#[derive(Clone)]
pub struct Engine {
    inner: Rc<Inner>,
}

impl Engine {
    pub fn new(cfg: Config, deps: Deps) -> Engine {
        let (cancel_browser, _) = watch::channel(true);
        Engine {
            inner: Rc::new(Inner {
                cfg: RefCell::new(cfg),
                status: RefCell::default(),
                deps,
                ipc: RefCell::default(),
                goxlr_conn: RefCell::default(),
                goxlr_status: RefCell::default(),
                snapshot: RefCell::default(),
                may_prompt: Cell::new(true),
                force_refresh: Cell::new(false),
                auth_failures: Cell::new(0),
                set_in_flight: Cell::new(false),
                action_in_flight: Cell::new(false),
                events_during_set: Cell::new(false),
                set_failures: Cell::new(0),
                retry: RefCell::default(),
                reconcile_fields: RefCell::default(),
                reconcile_needed: Flag::default(),
                discord_wakeup: Flag::default(),
                goxlr_restart: Flag::default(),
                ever_connected: RefCell::default(),
                loss_timers: RefCell::default(),
                loss_notified: RefCell::default(),
                pushed: RefCell::default(),
                push_lock: tokio::sync::Mutex::new(()),
                cancel_browser,
                last_signature: RefCell::default(),
                tasks: RefCell::default(),
            }),
        }
    }

    fn weak(&self) -> std::rc::Weak<Inner> {
        Rc::downgrade(&self.inner)
    }

    fn from_weak(weak: &std::rc::Weak<Inner>) -> Option<Engine> {
        weak.upgrade().map(|inner| Engine { inner })
    }

    // --- lifecycle ----------------------------------------------------------

    /// Runs until dropped (abort the task that awaits it).
    pub async fn run(&self) {
        let _cleanup = Cleanup(self.clone());
        tokio::join!(
            self.goxlr_loop(),
            self.discord_loop(),
            self.reconcile_loop()
        );
    }

    fn spawn(&self, future: impl std::future::Future<Output = ()> + 'static) {
        let mut tasks = self.inner.tasks.borrow_mut();
        tasks.retain(|t| !t.is_finished());
        tasks.push(spawn_local(future));
    }

    // --- public controls ----------------------------------------------------

    pub fn status(&self) -> Status {
        self.inner.status.borrow().clone()
    }

    pub fn config(&self) -> Config {
        self.inner.cfg.borrow().clone()
    }

    pub fn apply_config(&self, cfg: Config, credentials_changed: bool) {
        let old = self.inner.cfg.replace(cfg.clone());
        if credentials_changed || old.client_id != cfg.client_id || old.oauth_port != cfg.oauth_port
        {
            log::info!("Discord application settings changed, reconnecting");
            if old.client_id != cfg.client_id {
                self.inner.deps.tokens.clear(); // tokens belong to the previous application
            }
            self.inner.may_prompt.set(true);
            self.restart_discord();
        }
        if old.goxlr_url != cfg.goxlr_url {
            log::info!("GoXLR Utility address changed, reconnecting");
            self.inner.goxlr_restart.set();
        }
        let snap = self
            .inner
            .goxlr_status
            .borrow()
            .as_ref()
            .map(|s| s.snapshot(&cfg.mixer_serial, &cfg.deafen_channel));
        if let Some(snap) = snap {
            *self.inner.snapshot.borrow_mut() = snap;
        }
        // Re-enabling a sync pushes the current GoXLR state (other settings only
        // trigger a push when they change what the GoXLR wants).
        let enabled = (cfg.cough_sync && !old.cough_sync) || (cfg.chat_sync && !old.chat_sync);
        self.recompute(enabled);
        self.changed();
    }

    pub fn reauthorize(&self) {
        log::info!("Re-authorization requested");
        self.inner.deps.tokens.clear();
        self.inner.may_prompt.set(true);
        self.inner.auth_failures.set(0);
        self.restart_discord();
    }

    pub fn request_reconcile(&self, fields: &[&'static str]) {
        self.inner.reconcile_fields.borrow_mut().extend(fields);
        self.inner.reconcile_needed.set();
    }

    fn restart_discord(&self) {
        self.inner.cancel_browser.send_replace(true); // stop a browser authorization for the old settings
        if let Some(ipc) = self.inner.ipc.borrow().clone() {
            ipc.close();
        }
        self.inner.discord_wakeup.set();
    }

    // --- state helpers ------------------------------------------------------

    fn update(&self, change: impl FnOnce(&mut Status)) {
        change(&mut self.inner.status.borrow_mut());
        self.changed();
    }

    fn changed(&self) {
        let cfg = self.inner.cfg.borrow();
        let signature = (
            self.inner.status.borrow().clone(),
            cfg.cough_sync,
            cfg.chat_sync,
        );
        drop(cfg);
        let differs = self.inner.last_signature.borrow().as_ref() != Some(&signature);
        if differs {
            *self.inner.last_signature.borrow_mut() = Some(signature);
            (self.inner.deps.hooks.on_change)();
        }
    }

    fn notify(&self, title: &str, message: &str) {
        if self.inner.cfg.borrow().notifications {
            (self.inner.deps.hooks.notify)(title, message);
        }
    }

    fn connection_changed(&self, name: &'static str, up: bool) {
        let inner = &self.inner;
        if up {
            inner.ever_connected.borrow_mut().insert(name);
            if let Some(timer) = inner.loss_timers.borrow_mut().remove(name) {
                timer.abort();
            }
            if inner.loss_notified.borrow_mut().remove(name) {
                self.notify(
                    &tr("{name} reconnected", &[("name", name)]),
                    &tr("Synchronisation is running again.", &[]),
                );
            }
        } else if inner.ever_connected.borrow().contains(name)
            && !inner.loss_timers.borrow().contains_key(name)
        {
            let weak = self.weak();
            let delay = inner.deps.timing.loss_notify_delay;
            let timer = spawn_local(async move {
                tokio::time::sleep(delay).await;
                if let Some(engine) = Engine::from_weak(&weak) {
                    engine.loss_fire(name);
                }
            });
            inner.loss_timers.borrow_mut().insert(name, timer);
        }
    }

    fn loss_fire(&self, name: &'static str) {
        self.inner.loss_timers.borrow_mut().remove(name);
        let status = self.status();
        let up = if name == GOXLR {
            status.goxlr_connected
        } else {
            status.discord_connected
        };
        if !up {
            self.inner.loss_notified.borrow_mut().insert(name);
            self.notify(
                &tr("{name} disconnected", &[("name", name)]),
                &tr("Mute synchronisation is paused until it comes back.", &[]),
            );
        }
    }

    // --- GoXLR side ---------------------------------------------------------

    async fn goxlr_loop(&self) {
        let timing = self.inner.deps.timing.clone();
        let mut delay = timing.retry_min;
        loop {
            self.inner.goxlr_restart.clear();
            let url = self.inner.cfg.borrow().goxlr_url.clone();
            let weak = self.weak();
            let conn = self.inner.deps.goxlr.create(
                &url,
                Rc::new(move || {
                    if let Some(engine) = Engine::from_weak(&weak) {
                        engine.on_goxlr_update();
                    }
                }),
            );
            *self.inner.goxlr_conn.borrow_mut() = Some(conn.clone());
            let reason = tokio::select! {
                result = conn.run() => match result {
                    Ok(()) => "connection closed by GoXLR Utility".to_string(),
                    Err(e) => e,
                },
                _ = self.inner.goxlr_restart.wait() => "restart requested".to_string(),
            };
            *self.inner.goxlr_conn.borrow_mut() = None;
            drop(conn);

            let was_connected = self.inner.status.borrow().goxlr_connected;
            *self.inner.goxlr_status.borrow_mut() = None;
            *self.inner.snapshot.borrow_mut() = None;
            self.update(|s| {
                s.goxlr_connected = false;
                s.serial = None;
                s.deafen_fader = None;
            });
            self.recompute(false);
            self.connection_changed(GOXLR, false);
            if was_connected {
                log::warn!("GoXLR Utility disconnected ({reason})");
                delay = timing.retry_min;
            } else {
                log::info!("GoXLR Utility not available ({reason}), retrying in {delay:?}");
            }
            if self.inner.goxlr_restart.is_set() {
                continue;
            }
            tokio::select! {
                _ = tokio::time::sleep(delay) => {}
                _ = self.inner.goxlr_restart.wait() => {}
            }
            delay = (delay * 2).min(timing.retry_max);
        }
    }

    fn on_goxlr_update(&self) {
        let Some(conn) = self.inner.goxlr_conn.borrow().clone() else {
            return;
        };
        let Some(doc) = conn.status() else { return };
        let cfg = self.config();
        let previous = self.inner.snapshot.borrow().clone();
        let snap = doc.snapshot(&cfg.mixer_serial, &cfg.deafen_channel);
        *self.inner.goxlr_status.borrow_mut() = Some(doc);
        *self.inner.snapshot.borrow_mut() = snap.clone();
        if !self.inner.status.borrow().goxlr_connected {
            log::info!(
                "Connected to GoXLR Utility ({})",
                snap.as_ref().map_or("no device", |s| s.serial.as_str())
            );
            self.update(|s| s.goxlr_connected = true);
            self.connection_changed(GOXLR, true);
            if snap.as_ref().is_some_and(|s| s.deafen_fader.is_none()) {
                log::info!(
                    "Channel {} is not assigned to a fader: deafen sync inactive",
                    cfg.deafen_channel
                );
            }
        }
        if let (Some(prev), Some(snap)) = (&previous, &snap) {
            if prev.serial == snap.serial {
                for (action, button) in &cfg.button_actions {
                    if snap.buttons_down.contains(button) && !prev.buttons_down.contains(button) {
                        let label = BUTTON_ACTIONS
                            .iter()
                            .find(|(k, _)| k == action)
                            .map_or(action.as_str(), |(_, l)| l);
                        log::info!("GoXLR {button} pressed: {label}");
                        let engine = self.clone();
                        let action = action.clone();
                        self.spawn(async move { engine.run_action(&action).await });
                    }
                }
            }
            if prev.cough_state != snap.cough_state {
                log::info!(
                    "Cough button: {} -> {} (mute function {})",
                    shown(&prev.cough_state),
                    shown(&snap.cough_state),
                    shown(&snap.cough_mute_type)
                );
            }
            if prev.deafen_fader != snap.deafen_fader {
                log::info!(
                    "{} channel moved to fader {}",
                    cfg.deafen_channel,
                    shown(&snap.deafen_fader)
                );
            } else if prev.deafen_state != snap.deafen_state {
                log::info!(
                    "{} fader: {} -> {} (mute function {})",
                    cfg.deafen_channel,
                    shown(&prev.deafen_state),
                    shown(&snap.deafen_state),
                    shown(&snap.deafen_mute_type)
                );
            }
        }
        self.recompute(false);
    }

    fn recompute(&self, force_reconcile: bool) {
        let snap = self.inner.snapshot.borrow().clone();
        let cfg = self.config();
        let (mut mute, mut deaf) = (None, None);
        if let Some(snap) = &snap {
            mute = desired_flag(
                snap.cough_state.as_deref(),
                snap.cough_mute_type.as_deref(),
                &cfg.mute_triggers,
            );
            if snap.deafen_fader.is_some() {
                deaf = desired_flag(
                    snap.deafen_state.as_deref(),
                    snap.deafen_mute_type.as_deref(),
                    &cfg.deafen_triggers,
                );
            }
        }
        let status = self.status();
        let wanted_changed = (mute, deaf) != (status.goxlr_mute, status.goxlr_deaf);
        let have_status = self.inner.goxlr_status.borrow().is_some();
        let missing = (!cfg.mixer_serial.is_empty() && snap.is_none() && have_status)
            .then(|| cfg.mixer_serial.clone());
        if missing != status.missing_device {
            if let Some(serial) = &missing {
                let connected = self
                    .inner
                    .goxlr_status
                    .borrow()
                    .as_ref()
                    .map(|s| s.mixers().join(", "))
                    .unwrap_or_default();
                log::warn!(
                    "GoXLR {serial} is not connected (connected: {}): sync paused",
                    if connected.is_empty() {
                        "none"
                    } else {
                        &connected
                    }
                );
            } else if let Some(serial) = &status.missing_device {
                log::info!("GoXLR {serial} is back");
            }
        }
        self.update(|s| {
            s.missing_device = missing;
            s.goxlr_mute = mute;
            s.goxlr_deaf = deaf;
            s.serial = snap.as_ref().map(|s| s.serial.clone());
            s.deafen_fader = snap.as_ref().and_then(|s| s.deafen_fader.clone());
        });
        // Only GoXLR changes that matter trigger a push: volume moves must not
        // override a mute the user toggled directly in Discord.
        if wanted_changed || force_reconcile {
            self.request_reconcile(&[MUTE, DEAF]);
        }
    }

    // --- Discord side -------------------------------------------------------

    async fn discord_loop(&self) {
        let timing = self.inner.deps.timing.clone();
        let mut delay = timing.retry_min;
        loop {
            self.inner.discord_wakeup.clear();
            let ended = self.discord_session().await;
            let was_connected = self.inner.status.borrow().discord_connected;
            let wait_for_user = match ended {
                End::NeedsUser(reason) => {
                    log::warn!("{reason}");
                    self.update(|s| s.detail = reason);
                    true
                }
                End::Failed(reason) => {
                    if was_connected {
                        log::warn!("Discord connection lost: {reason}");
                    } else {
                        log::info!("Discord not available: {reason} (retrying in {delay:?})");
                    }
                    self.update(|s| s.detail = format!("Discord: {reason}"));
                    false
                }
            };
            self.update(|s| {
                s.discord_connected = false;
                s.discord_mute = None;
                s.discord_deaf = None;
                s.voice_channel = None;
            });
            self.connection_changed(DISCORD, false);
            if was_connected {
                delay = timing.retry_min;
            }
            if wait_for_user {
                self.inner.discord_wakeup.wait().await;
                delay = timing.retry_min;
                continue;
            }
            if !self.inner.discord_wakeup.is_set() {
                tokio::select! {
                    _ = tokio::time::sleep(delay) => {}
                    _ = self.inner.discord_wakeup.wait() => {}
                }
            }
            delay = (delay * 2).min(timing.retry_max);
        }
    }

    async fn discord_session(&self) -> End {
        let cfg = self.config();
        let secret = (self.inner.deps.get_secret)();
        if cfg.client_id.is_empty() || secret.is_empty() {
            self.update(|s| s.auth = Auth::NotConfigured);
            return End::NeedsUser(
                "Setup required: enter your Discord application in Settings".into(),
            );
        }
        let weak = self.weak();
        let sink: EventSink = Rc::new(move |evt: &str, data: Value| {
            if let Some(engine) = Engine::from_weak(&weak) {
                engine.on_discord_event(evt, data);
            }
        });
        let ipc = match self.inner.deps.discord.connect(&cfg.client_id, sink).await {
            Ok(ipc) => ipc,
            Err(e) => return End::Failed(e.to_string()),
        };
        *self.inner.ipc.borrow_mut() = Some(ipc.clone());
        let ended = self.discord_session_on(&*ipc, &cfg, &secret).await;
        *self.inner.ipc.borrow_mut() = None;
        ipc.close();
        ended
    }

    async fn discord_session_on(
        &self,
        ipc: &dyn DiscordConnection,
        cfg: &Config,
        secret: &str,
    ) -> End {
        let tokens = self.inner.deps.tokens.clone();
        let force = self.inner.force_refresh.replace(false);
        let token = match tokens.access_token(&cfg.client_id, secret, force).await {
            Ok(Some(token)) => token,
            Ok(None) => match self.interactive_authorize(ipc, cfg, secret).await {
                Ok(token) => token,
                Err(end) => return end,
            },
            Err(e) => return End::Failed(e.to_string()),
        };
        let rpc = Rpc(ipc);
        match rpc.authenticate(&token).await {
            Ok(_) => {}
            Err(RpcError::Rpc { message, .. }) => {
                let failures = self.inner.auth_failures.get() + 1;
                self.inner.auth_failures.set(failures);
                self.inner.force_refresh.set(true);
                if failures >= 2 {
                    log::warn!("Discord keeps rejecting the token, a new authorization is needed");
                    tokens.clear();
                }
                return End::Failed(format!("Discord rejected the access token: {message}"));
            }
            Err(e) => return End::Failed(e.to_string()),
        }
        self.inner.auth_failures.set(0);
        let voice = match rpc.get_voice_settings().await {
            Ok(v) => v,
            Err(e) => return End::Failed(e.to_string()),
        };
        if let Err(e) = rpc.subscribe("VOICE_SETTINGS_UPDATE").await {
            return End::Failed(e.to_string());
        }
        self.update_discord_voice(&voice);
        match rpc.subscribe("VOICE_CHANNEL_SELECT").await {
            Ok(_) => {}
            Err(RpcError::Rpc { message, .. }) => {
                log::info!("Voice channel tracking unavailable: {message}")
            }
            Err(e) => return End::Failed(e.to_string()),
        }
        self.refresh_voice_channel(ipc).await;
        log::info!("Connected to Discord");
        self.update(|s| {
            s.auth = Auth::Ok;
            s.discord_connected = true;
            s.detail = "Connected".into();
        });
        self.connection_changed(DISCORD, true);
        self.request_reconcile(&[MUTE, DEAF]);
        End::Failed(format!("closed: {}", ipc.wait_closed().await))
    }

    async fn interactive_authorize(
        &self,
        ipc: &dyn DiscordConnection,
        cfg: &Config,
        secret: &str,
    ) -> Result<String, End> {
        let inner = &self.inner;
        if !inner.may_prompt.get() {
            self.update(|s| s.auth = Auth::Required);
            return Err(End::NeedsUser(
                "Discord authorization required: choose 'Authorize Discord' in the tray menu"
                    .into(),
            ));
        }
        inner.may_prompt.set(false);
        self.update(|s| {
            s.auth = Auth::Authorizing;
            s.detail = "Waiting for your approval in Discord".into();
        });
        self.notify(
            &tr("Discord authorization", &[]),
            &tr("Approve GoXLR Discord Sync in the Discord window.", &[]),
        );
        let tokens = inner.deps.tokens.clone();
        let redirect = cfg.redirect_uri();
        // Not the user's answer: prompt again on the next attempt.
        let retry_later = |reason: String| {
            inner.may_prompt.set(true);
            self.update(|s| s.auth = Auth::Unknown);
            End::Failed(reason)
        };
        match Rpc(ipc).authorize(&cfg.client_id, &SCOPES).await {
            Ok(code) => match tokens
                .exchange(
                    &cfg.client_id,
                    secret,
                    &code,
                    &[Some(redirect.clone()), None],
                )
                .await
            {
                Ok(token) => return Ok(token),
                Err(AuthError::Transient(e)) => return Err(retry_later(e)),
                Err(AuthError::Refused(e)) => {
                    log::warn!("Code exchange failed ({e}), trying the browser")
                }
            },
            Err(RpcError::Closed(e) | RpcError::Unavailable(e)) => return Err(retry_later(e)),
            Err(RpcError::Timeout) => {
                self.update(|s| s.auth = Auth::Required);
                return Err(End::NeedsUser(
                    "The authorization request in Discord was not answered".into(),
                ));
            }
            Err(RpcError::Rpc { message, .. }) => {
                let text = message.to_lowercase();
                if ["denied", "cancel", "reject"]
                    .iter()
                    .any(|w| text.contains(w))
                {
                    self.update(|s| s.auth = Auth::Required);
                    return Err(End::NeedsUser(
                        "Authorization declined in Discord. Use 'Authorize Discord' to try again."
                            .into(),
                    ));
                }
                log::warn!("In-app authorization failed ({message}), trying the browser");
            }
        }

        self.update(|s| s.detail = "Waiting for authorization in your browser".into());
        self.notify(
            &tr("Discord authorization", &[]),
            &tr("Approve GoXLR Discord Sync in your browser.", &[]),
        );
        inner.cancel_browser.send_replace(false);
        let code = tokens
            .browser_authorize(
                &cfg.client_id,
                &redirect,
                cfg.oauth_port,
                inner.deps.timing.browser_timeout,
                inner.cancel_browser.subscribe(),
            )
            .await;
        inner.cancel_browser.send_replace(true);
        let result = match code {
            Ok(code) => {
                tokens
                    .exchange(&cfg.client_id, secret, &code, &[Some(redirect)])
                    .await
            }
            Err(e) => Err(e),
        };
        match result {
            Ok(token) => Ok(token),
            Err(AuthError::Transient(e)) => Err(retry_later(e)),
            Err(AuthError::Refused(e)) => {
                self.update(|s| s.auth = Auth::Required);
                Err(End::NeedsUser(format!("Discord authorization failed: {e}")))
            }
        }
    }

    fn update_discord_voice(&self, data: &Value) {
        let (mute, deaf) = (data["mute"].as_bool(), data["deaf"].as_bool());
        if mute.is_some() || deaf.is_some() {
            self.update(|s| {
                if mute.is_some() {
                    s.discord_mute = mute;
                }
                if deaf.is_some() {
                    s.discord_deaf = deaf;
                }
            });
        }
    }

    /// Name of the voice channel the user is in (tooltip, Status, update timing).
    async fn refresh_voice_channel(&self, ipc: &dyn DiscordConnection) {
        let rpc = Rpc(ipc);
        let channel = match rpc.get_selected_voice_channel().await {
            Ok(c) => c,
            Err(e) => {
                log::info!("Cannot read the voice channel: {e}");
                return;
            }
        };
        let mut label = None;
        if channel["id"].as_str().is_some() {
            let name = channel["name"]
                .as_str()
                .filter(|n| !n.is_empty())
                .unwrap_or("Private call")
                .to_string();
            label = Some(name.clone());
            if let Some(guild_id) = channel["guild_id"].as_str() {
                match rpc.get_guild(guild_id).await {
                    Ok(guild) => {
                        if let Some(server) = guild["name"].as_str() {
                            label = Some(format!("{server} › {name}"));
                        }
                    }
                    Err(e) => {
                        log::info!("Cannot read the voice channel: {e}");
                        return;
                    }
                }
            }
        }
        if label != self.inner.status.borrow().voice_channel {
            log::info!("Voice channel: {}", label.as_deref().unwrap_or("none"));
        }
        self.update(|s| s.voice_channel = label);
    }

    /// Discord action bound to a GoXLR button.
    async fn run_action(&self, action: &str) {
        let ipc = self.inner.ipc.borrow().clone();
        let status = self.status();
        let Some(ipc) = ipc.filter(|_| status.discord_connected) else {
            log::info!("Discord is not connected: {action} ignored");
            return;
        };
        let rpc = Rpc(&*ipc);
        let result = match action {
            "toggle_mute" | "toggle_deafen" => {
                let (prev_mute, prev_deaf) = (status.discord_mute, status.discord_deaf);
                let args = if action == "toggle_mute" {
                    json!({MUTE: prev_mute != Some(true)})
                } else {
                    json!({DEAF: prev_deaf != Some(true)})
                };
                self.inner.action_in_flight.set(true);
                let result = rpc.set_voice_settings(args.clone()).await;
                self.inner.action_in_flight.set(false);
                result.map(|answer| {
                    let answer = if answer["mute"].is_boolean() || answer["deaf"].is_boolean() {
                        answer
                    } else {
                        args
                    };
                    self.update_discord_voice(&answer);
                    // A user choice made on the GoXLR: handled once, whatever the message order.
                    self.after_voice_change(prev_mute, prev_deaf);
                })
            }
            "leave_voice" => {
                if status.voice_channel.is_none() {
                    log::info!("Not in a voice channel");
                    return;
                }
                rpc.select_voice_channel(None).await.map(|_| ())
            }
            _ => Ok(()),
        };
        if let Err(e) = result {
            log::warn!("Discord action {action} failed: {e}");
        }
    }

    fn on_discord_event(&self, evt: &str, data: Value) {
        if evt == "VOICE_CHANNEL_SELECT" {
            if let Some(ipc) = self.inner.ipc.borrow().clone() {
                let engine = self.clone();
                self.spawn(async move { engine.refresh_voice_channel(&*ipc).await });
            }
            return;
        }
        if evt != "VOICE_SETTINGS_UPDATE" {
            return;
        }
        let status = self.status();
        self.update_discord_voice(&data);
        if self.inner.action_in_flight.get() {
            return; // run_action handles it when the response arrives
        }
        if self.inner.set_in_flight.get() {
            // Echo of our own change (Discord may send it before the response), or a click
            // at the same instant: settled by the reconcile loop once the response is in.
            self.inner.events_during_set.set(true);
            return;
        }
        self.after_voice_change(status.discord_mute, status.discord_deaf);
    }

    /// React to a voice settings change made by the user (in Discord or with a button).
    fn after_voice_change(&self, prev_mute: Option<bool>, prev_deaf: Option<bool>) {
        let st = self.status();
        // Only fields that actually changed come from the user; everything else is a repeat.
        let deaf_changed = prev_deaf.is_some() && st.discord_deaf != prev_deaf;
        let mute_changed = prev_mute.is_some() && st.discord_mute != prev_mute;
        if deaf_changed && st.discord_deaf != Some(true) {
            // Undeafen: Discord restores its own pre-deafen mute, which says nothing about
            // what the GoXLR wants. Re-apply the GoXLR mute (never leave a live mic behind).
            self.request_reconcile(&[MUTE]);
        }
        if !self.inner.cfg.borrow().bidirectional {
            return;
        }
        // A mute change implied by deafening/undeafening is not a user choice.
        let push_deaf = deaf_changed;
        let push_mute = mute_changed && !deaf_changed && st.discord_deaf != Some(true);
        if push_deaf || push_mute {
            let engine = self.clone();
            self.spawn(async move { engine.push_discord_to_goxlr(push_mute, push_deaf).await });
        }
    }

    // --- reconciliation -----------------------------------------------------

    async fn reconcile_loop(&self) {
        loop {
            self.inner.reconcile_needed.wait().await;
            self.inner.reconcile_needed.clear();
            let fields = std::mem::take(&mut *self.inner.reconcile_fields.borrow_mut());
            self.reconcile(fields).await;
        }
    }

    fn schedule_retry(&self, fields: BTreeSet<&'static str>) {
        let timing = &self.inner.deps.timing;
        let failures = self.inner.set_failures.get();
        let delay = (timing.retry_min * 2u32.saturating_pow(failures)).min(timing.retry_max);
        self.inner.set_failures.set(failures + 1);
        let weak = self.weak();
        let handle = spawn_local(async move {
            tokio::time::sleep(delay).await;
            if let Some(engine) = Engine::from_weak(&weak) {
                let fields: Vec<&'static str> = fields.into_iter().collect();
                engine.request_reconcile(&fields);
            }
        });
        if let Some(previous) = self.inner.retry.borrow_mut().replace(handle) {
            previous.abort();
        }
        log::info!("Retrying the Discord update in {delay:?}");
    }

    async fn reconcile(&self, mut fields: BTreeSet<&'static str>) {
        for _ in 0..4 {
            // undeafen, then mute, plus a re-check after events during a set
            let ipc = self.inner.ipc.borrow().clone();
            let status = self.status();
            let Some(ipc) = ipc.filter(|_| status.discord_connected) else {
                return;
            };
            let cfg = self.config();
            let want_mute = if cfg.cough_sync && fields.contains(MUTE) {
                status.goxlr_mute
            } else {
                None
            };
            let want_deaf = if cfg.chat_sync && fields.contains(DEAF) {
                status.goxlr_deaf
            } else {
                None
            };
            let Some(change) = plan_discord_update(
                want_mute,
                want_deaf,
                status.discord_mute,
                status.discord_deaf,
            ) else {
                self.inner.set_failures.set(0);
                return;
            };
            let args = json!({change.field(): change.value()});
            let started = Instant::now();
            self.inner.set_in_flight.set(true);
            self.inner.events_during_set.set(false);
            let result = Rpc(&*ipc).set_voice_settings(args.clone()).await;
            self.inner.set_in_flight.set(false);
            let answer = match result {
                Ok(answer) => answer,
                Err(RpcError::Closed(e) | RpcError::Unavailable(e)) => {
                    log::warn!("Discord closed before applying {args}: {e}");
                    return; // the new session pushes the state again
                }
                Err(e) => {
                    log::warn!("Discord did not apply {args}: {e}");
                    self.update(|s| s.detail = format!("Sync failed: {e}"));
                    self.schedule_retry(fields);
                    return;
                }
            };
            let latency = started.elapsed().as_secs_f64() * 1000.0;
            self.update_discord_voice(
                if answer["mute"].is_boolean() || answer["deaf"].is_boolean() {
                    &answer
                } else {
                    &args
                },
            );
            log::info!(
                "Discord {}={} ({latency:.0} ms)",
                change.field(),
                change.value()
            );
            self.update(|s| {
                s.last_latency_ms = Some(latency);
                s.detail = "Synchronised".into();
            });
            if self.inner.events_during_set.get() {
                // Something else may have changed meanwhile: the GoXLR state decides.
                fields = [MUTE, DEAF].into_iter().collect();
            }
        }
    }

    /// Two-way sync: mirror a change the user made in Discord onto the GoXLR.
    async fn push_discord_to_goxlr(&self, push_mute: bool, push_deaf: bool) {
        let _guard = self.inner.push_lock.lock().await;
        let conn = self.inner.goxlr_conn.borrow().clone();
        let snap = self.inner.snapshot.borrow().clone();
        let (Some(conn), Some(snap)) = (conn, snap) else {
            return;
        };
        if !conn.connected() {
            return;
        }
        let (st, cfg) = (self.status(), self.config());
        let result = async {
            if push_deaf
                && cfg.chat_sync
                && st.discord_deaf.is_some()
                && st.goxlr_deaf.is_some()
                && st.discord_deaf != st.goxlr_deaf
            {
                if let Some(fader) = snap.deafen_fader.clone() {
                    self.push_state(
                        &*conn,
                        &snap.serial,
                        "fader",
                        st.discord_deaf == Some(true),
                        snap.deafen_state.as_deref(),
                        snap.deafen_mute_type.as_deref(),
                        &cfg.deafen_triggers,
                        |state| json!({"SetFaderMuteState": [fader, state]}),
                    )
                    .await?;
                }
            }
            if push_mute
                && cfg.cough_sync
                && st.discord_deaf != Some(true)
                && st.discord_mute.is_some()
                && st.goxlr_mute.is_some()
                && st.discord_mute != st.goxlr_mute
            {
                self.push_state(
                    &*conn,
                    &snap.serial,
                    "cough",
                    st.discord_mute == Some(true),
                    snap.cough_state.as_deref(),
                    snap.cough_mute_type.as_deref(),
                    &cfg.mute_triggers,
                    |state| json!({"SetCoughMuteState": state}),
                )
                .await?;
            }
            Ok::<(), String>(())
        }
        .await;
        if let Err(e) = result {
            log::warn!("Could not update the GoXLR: {e}");
        }
    }

    #[allow(clippy::too_many_arguments)]
    async fn push_state(
        &self,
        conn: &dyn GoxlrConnection,
        serial: &str,
        key: &'static str,
        muted: bool,
        current: Option<&str>,
        mute_type: Option<&str>,
        triggers: &[String],
        command: impl Fn(&str) -> Value,
    ) -> Result<(), String> {
        let target = if muted {
            let Some(target) = goxlr_state_for(true, mute_type, triggers) else {
                log::info!(
                    "No GoXLR mute state matches the Discord change with the current settings"
                );
                return Ok(());
            };
            // Remember a mute that was not synced (e.g. Muted to Stream) to restore it later.
            let restore = current
                .filter(|c| *c != "Unmuted")
                .unwrap_or("Unmuted")
                .to_string();
            self.inner
                .pushed
                .borrow_mut()
                .insert(key, (target.to_string(), restore));
            target.to_string()
        } else {
            let pushed = self.inner.pushed.borrow_mut().remove(key);
            match pushed {
                Some((set, restore)) if Some(set.as_str()) == current => restore,
                _ => "Unmuted".to_string(),
            }
        };
        log::info!(
            "Discord {} changed: setting {key} to {target}",
            if key == "fader" { "deafen" } else { "mute" }
        );
        conn.send_command(serial, command(&target)).await
    }

    // --- presentation -------------------------------------------------------

    pub fn summary(&self) -> String {
        let st = self.status();
        match st.auth {
            Auth::NotConfigured => return tr("Setup required", &[]),
            Auth::Authorizing => return tr("Waiting for Discord authorization", &[]),
            Auth::Required => return tr("Discord authorization required", &[]),
            _ => {}
        }
        if !st.discord_connected {
            return tr("Waiting for Discord", &[]);
        }
        if !st.goxlr_connected {
            return tr("Waiting for GoXLR Utility", &[]);
        }
        if let Some(serial) = &st.missing_device {
            return tr("GoXLR {serial} not connected", &[("serial", serial)]);
        }
        if st.serial.is_none() {
            return tr("No GoXLR connected", &[]);
        }
        let mic = if st.discord_mute == Some(true) {
            tr("Mic muted", &[])
        } else {
            tr("Mic live", &[])
        };
        let audio = if st.discord_deaf == Some(true) {
            tr("deafened", &[])
        } else {
            tr("hearing", &[])
        };
        let place = st
            .voice_channel
            .map(|c| format!(" - {c}"))
            .unwrap_or_default();
        format!("{mic}, {audio}{place}")
    }

    pub fn icon_spec(&self) -> IconSpec {
        let st = self.status();
        if matches!(
            st.auth,
            Auth::NotConfigured | Auth::Required | Auth::Authorizing
        ) {
            return IconSpec {
                color: IconColor::Idle,
                glyph: Glyph::Mic,
                slashed: false,
            };
        }
        let (mute, deaf) = if st.discord_connected {
            (st.discord_mute, st.discord_deaf)
        } else {
            (st.goxlr_mute, st.goxlr_deaf)
        };
        let mut spec = if deaf == Some(true) {
            IconSpec {
                color: IconColor::Muted,
                glyph: Glyph::Headphones,
                slashed: true,
            }
        } else if mute == Some(true) {
            IconSpec {
                color: IconColor::Muted,
                glyph: Glyph::Mic,
                slashed: true,
            }
        } else {
            IconSpec {
                color: IconColor::Live,
                glyph: Glyph::Mic,
                slashed: false,
            }
        };
        if !(st.discord_connected && st.goxlr_connected) || st.serial.is_none() {
            spec.color = IconColor::Warning;
        }
        spec
    }

    pub fn details(&self) -> String {
        let st = self.status();
        let goxlr = if let (true, Some(serial)) = (st.goxlr_connected, &st.serial) {
            tr("connected ({serial})", &[("serial", serial)])
        } else if let Some(serial) = &st.missing_device {
            tr(
                "connected, but GoXLR {serial} is not plugged in",
                &[("serial", serial)],
            )
        } else if st.goxlr_connected {
            tr("connected, no device", &[])
        } else {
            tr("not available", &[])
        };
        let discord = if st.discord_connected {
            tr("connected", &[])
        } else {
            self.summary()
        };
        let mut lines = vec![
            tr("GoXLR Utility: {state}", &[("state", &goxlr)]),
            tr("Discord: {state}", &[("state", &discord)]),
        ];
        if st.discord_connected {
            let mic = if st.discord_mute == Some(true) {
                tr("muted", &[])
            } else {
                tr("live", &[])
            };
            let audio = if st.discord_deaf == Some(true) {
                tr("deafened", &[])
            } else {
                tr("hearing", &[])
            };
            lines.push(tr(
                "Mic: {mic} · Audio: {audio}",
                &[("mic", &mic), ("audio", &audio)],
            ));
            let none = tr("none", &[]);
            lines.push(tr(
                "Voice channel: {channel}",
                &[("channel", st.voice_channel.as_deref().unwrap_or(&none))],
            ));
        }
        let cfg = self.config();
        if st.goxlr_connected && cfg.chat_sync {
            let place = match &st.deafen_fader {
                Some(fader) => tr("fader {fader}", &[("fader", fader)]),
                None => tr("not on a fader", &[]),
            };
            lines.push(tr(
                "{channel} channel: {where}",
                &[("channel", &cfg.deafen_channel), ("where", &place)],
            ));
        }
        if let Some(ms) = st.last_latency_ms {
            lines.push(tr("Last sync: {ms} ms", &[("ms", &format!("{ms:.0}"))]));
        }
        lines.join("\n")
    }
}

/// Stops timers and helper tasks when `run` is dropped.
struct Cleanup(Engine);

impl Drop for Cleanup {
    fn drop(&mut self) {
        let inner = &self.0.inner;
        inner.cancel_browser.send_replace(true);
        for (_, timer) in inner.loss_timers.borrow_mut().drain() {
            timer.abort();
        }
        if let Some(retry) = inner.retry.borrow_mut().take() {
            retry.abort();
        }
        for task in inner.tasks.borrow_mut().drain(..) {
            task.abort();
        }
        if let Some(ipc) = inner.ipc.borrow_mut().take() {
            ipc.close();
        }
    }
}

impl VoiceChange {
    pub fn as_json(self) -> Value {
        json!({self.field(): self.value()})
    }
}

/// GoXLR value for the log, as the Python version wrote it.
fn shown(value: &Option<String>) -> &str {
    value.as_deref().unwrap_or("None")
}
