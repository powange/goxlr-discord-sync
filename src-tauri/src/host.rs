//! Runs the engine on its own thread (a single-threaded tokio runtime + LocalSet,
//! like the engine expects) and shares its state with the tray and the settings.

use gds_core::auth::DiscordTokens;
use gds_core::config::{config_file, data_dir, Config};
use gds_core::discord::DiscordIpcConnector;
use gds_core::engine::{Deps, Engine, Hooks, IconSpec, Status, Timing};
use gds_core::goxlr::GoxlrClientConnector;
use gds_core::secrets::SecretsFile;
use std::cell::RefCell;
use std::rc::Rc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};
use tauri::AppHandle;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;
use tokio::sync::mpsc;

#[allow(clippy::large_enum_variant)] // rare messages, boxing buys nothing
pub enum Cmd {
    ApplyConfig {
        cfg: Config,
        credentials_changed: bool,
    },
    Reauthorize,
}

/// What the tray and the settings window read (updated by the engine thread).
#[derive(Clone)]
pub struct Shared {
    pub summary: String,
    pub details: String,
    pub icon: IconSpec,
    pub status: Status,
    pub cfg: Config,
    /// Version of an update that is available (menu entry).
    pub update: Option<String>,
    /// An automatic update notice waiting for the end of a call.
    pub deferred_update_notice: Option<String>,
}

pub struct Host {
    tx: mpsc::UnboundedSender<Cmd>,
    pub shared: Arc<Mutex<Shared>>,
}

impl Host {
    pub fn send(&self, cmd: Cmd) {
        let _ = self.tx.send(cmd);
    }

    pub fn snapshot(&self) -> Shared {
        self.shared.lock().unwrap().clone()
    }

    pub fn in_call(&self) -> bool {
        self.shared.lock().unwrap().status.voice_channel.is_some()
    }
}

pub fn secrets() -> SecretsFile {
    SecretsFile::new(data_dir())
}

fn mtime(path: &std::path::Path) -> Option<SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

pub fn start(app: AppHandle, cfg: Config) -> Host {
    let (tx, rx) = mpsc::unbounded_channel();
    let shared = Arc::new(Mutex::new(Shared {
        summary: String::new(),
        details: String::new(),
        icon: IconSpec {
            color: gds_core::engine::IconColor::Idle,
            glyph: gds_core::engine::Glyph::Mic,
            slashed: false,
        },
        status: Status::default(),
        cfg: cfg.clone(),
        update: None,
        deferred_update_notice: None,
    }));
    let thread_shared = shared.clone();
    std::thread::Builder::new()
        .name("engine".into())
        .spawn(move || {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("engine runtime");
            let local = tokio::task::LocalSet::new();
            local.block_on(&runtime, run(app, cfg, rx, thread_shared));
        })
        .expect("engine thread");
    Host { tx, shared }
}

async fn run(
    app: AppHandle,
    cfg: Config,
    mut rx: mpsc::UnboundedReceiver<Cmd>,
    shared: Arc<Mutex<Shared>>,
) {
    let secrets = Rc::new(secrets());
    let handle: Rc<RefCell<Option<Engine>>> = Rc::default();
    let opener = app.clone();
    let tokens = Rc::new(DiscordTokens::new(
        secrets.clone(),
        Rc::new(move |url: &str| {
            if let Err(e) = opener.opener().open_url(url, None::<&str>) {
                log::warn!("Cannot open the browser: {e}");
            }
        }),
    ));
    let (change_app, change_shared, change_handle) = (app.clone(), shared.clone(), handle.clone());
    let notify_app = app.clone();
    let secret_store = secrets.clone();
    let engine = Engine::new(
        cfg,
        Deps {
            tokens,
            get_secret: Box::new(move || secret_store.client_secret()),
            discord: Rc::new(DiscordIpcConnector),
            goxlr: Rc::new(GoxlrClientConnector),
            hooks: Hooks {
                on_change: Box::new(move || {
                    if let Some(engine) = change_handle.borrow().as_ref() {
                        publish(&change_app, &change_shared, engine);
                    }
                }),
                notify: Box::new(move |title, body| show_notification(&notify_app, title, body)),
            },
            timing: Timing::default(),
        },
    );
    *handle.borrow_mut() = Some(engine.clone());
    publish(&app, &shared, &engine);
    let runner = engine.clone();
    tokio::task::spawn_local(async move { runner.run().await });
    tokio::task::spawn_local(watch_files(engine.clone(), secrets.clone()));
    while let Some(cmd) = rx.recv().await {
        match cmd {
            Cmd::ApplyConfig {
                cfg,
                credentials_changed,
            } => engine.apply_config(cfg, credentials_changed),
            Cmd::Reauthorize => engine.reauthorize(),
        }
        publish(&app, &shared, &engine);
    }
}

fn publish(app: &AppHandle, shared: &Arc<Mutex<Shared>>, engine: &Engine) {
    let pending_notice = {
        let mut s = shared.lock().unwrap();
        s.summary = engine.summary();
        s.details = engine.details();
        s.icon = engine.icon_spec();
        s.status = engine.status();
        s.cfg = engine.config();
        if s.status.voice_channel.is_none() {
            s.deferred_update_notice.take()
        } else {
            None
        }
    };
    if let Some(version) = pending_notice {
        crate::updates::notify_available(app, &version);
    }
    let tray_app = app.clone();
    let _ = app.run_on_main_thread(move || crate::tray::refresh(&tray_app));
}

pub fn show_notification(app: &AppHandle, title: &str, body: &str) {
    if let Err(e) = app.notification().builder().title(title).body(body).show() {
        log::info!("Notification not shown ({e}): {title} - {body}");
    }
}

/// Pick up config.json / secrets.dat changes made outside the settings window.
async fn watch_files(engine: Engine, secrets: Rc<SecretsFile>) {
    let (cfg_path, secrets_path) = (config_file(), secrets.path());
    let (mut cfg_seen, mut secrets_seen, mut secret) = (
        mtime(&cfg_path),
        mtime(&secrets_path),
        secrets.client_secret(),
    );
    loop {
        tokio::time::sleep(Duration::from_secs(2)).await;
        let (cfg_now, secrets_now) = (mtime(&cfg_path), mtime(&secrets_path));
        let mut credentials_changed = false;
        if secrets_now != secrets_seen {
            secrets_seen = secrets_now;
            let new_secret = secrets.client_secret();
            credentials_changed = new_secret != secret;
            secret = new_secret;
        }
        if cfg_now != cfg_seen || credentials_changed {
            cfg_seen = cfg_now;
            let cfg = Config::load(&cfg_path);
            if cfg != engine.config() || credentials_changed {
                log::info!("Settings reloaded");
                engine.apply_config(cfg, credentials_changed);
            }
        }
    }
}
