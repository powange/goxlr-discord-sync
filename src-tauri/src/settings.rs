//! The settings window (ui/settings.html) and the commands it calls.

use crate::autostart;
use gds_core::config::{
    self, action_buttons, config_file, log_dir, Config, BUTTON_ACTIONS, FADER_CHANNELS,
    MUTE_TARGETS,
};
use gds_core::goxlr::Device;
use gds_core::i18n::{self, tr, LANGUAGES};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

use crate::host::{secrets, Cmd, Host};

pub fn open(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("settings") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    // Debug builds can open a given tab (screenshots in tests): GDS_SETTINGS_TAB=goxlr.
    let page = match std::env::var("GDS_SETTINGS_TAB") {
        Ok(tab) if cfg!(debug_assertions) => format!("settings.html#{tab}"),
        _ => "settings.html".to_string(),
    };
    let built = WebviewWindowBuilder::new(app, "settings", WebviewUrl::App(page.into()))
        .title(tr("{app} – Settings", &[("app", "GoXLR Discord Sync")]))
        .inner_size(760.0, 640.0)
        .min_inner_size(640.0, 560.0)
        .center()
        .build();
    match built {
        Ok(window) => log::info!(
            "Settings window opened (visible: {:?})",
            window.is_visible()
        ),
        Err(e) => log::error!("Cannot open the settings window: {e}"),
    }
}

#[derive(Serialize)]
pub struct SettingsData {
    config: Value,
    client_secret: String,
    redirect_uri: String,
    autostart: bool,
    platform: &'static str,
    version: &'static str,
    language: &'static str,
    /// English text -> translation (empty in English).
    texts: HashMap<String, String>,
    mute_targets: Vec<(&'static str, &'static str)>,
    fader_channels: Vec<&'static str>,
    action_buttons: Vec<(&'static str, &'static str)>,
    button_actions: Vec<(&'static str, &'static str)>,
    languages: Vec<(&'static str, &'static str)>,
    default_goxlr_url: &'static str,
}

#[tauri::command]
pub fn get_settings(app: AppHandle, host: tauri::State<'_, Host>) -> SettingsData {
    let cfg = host.snapshot().cfg;
    SettingsData {
        redirect_uri: cfg.redirect_uri(),
        config: cfg.to_value(),
        client_secret: secrets().client_secret(),
        autostart: autostart::is_enabled(&app),
        platform: if cfg!(windows) {
            "windows"
        } else if cfg!(target_os = "macos") {
            "macos"
        } else {
            "linux"
        },
        version: env!("CARGO_PKG_VERSION"),
        language: i18n::current(),
        texts: i18n::catalog_for_ui(),
        mute_targets: MUTE_TARGETS.to_vec(),
        fader_channels: FADER_CHANNELS.to_vec(),
        action_buttons: action_buttons().collect(),
        button_actions: BUTTON_ACTIONS.to_vec(),
        languages: LANGUAGES.to_vec(),
        default_goxlr_url: config::DEFAULT_GOXLR_URL,
    }
}

#[derive(Deserialize)]
pub struct SavePayload {
    config: Value,
    client_secret: String,
    autostart: bool,
}

#[tauri::command]
pub fn save_settings(
    app: AppHandle,
    host: tauri::State<'_, Host>,
    payload: SavePayload,
) -> Result<(), String> {
    let old = host.snapshot().cfg;
    let cfg = Config::from_value(&payload.config);
    if !cfg.goxlr_url.starts_with("ws://") && !cfg.goxlr_url.starts_with("wss://") {
        return Err(tr(
            "The address must start with ws:// (default: {url}).",
            &[("url", config::DEFAULT_GOXLR_URL)],
        ));
    }
    let store = secrets();
    let secret = payload.client_secret.trim();
    let mut credentials_changed = false;
    let mut save = || -> std::io::Result<()> {
        use gds_core::auth::TokenStore;
        if cfg.client_id != old.client_id {
            store.set_token(None)?; // tokens belong to the previous application
        }
        if secret != store.client_secret() {
            store.set_client_secret(secret)?;
            credentials_changed = true;
        }
        cfg.save(&config_file())
    };
    save().map_err(|e| format!("{}\n{e}", tr("Could not save the settings:", &[])))?;
    if payload.autostart != autostart::is_enabled(&app) {
        autostart::set_enabled(&app, payload.autostart)
            .map_err(|e| format!("{}\n{e}", tr("Could not save the settings:", &[])))?;
    }
    if cfg.language != old.language {
        log::info!("Interface language: {}", i18n::set_language(&cfg.language));
    }
    log::info!("Settings saved");
    host.send(Cmd::ApplyConfig {
        cfg,
        credentials_changed,
    });
    Ok(())
}

#[tauri::command]
pub async fn check_credentials(client_id: String, client_secret: String) -> (bool, String) {
    gds_core::auth::check_credentials_at(
        gds_core::auth::API,
        client_id.trim(),
        client_secret.trim(),
    )
    .await
}

#[tauri::command]
pub async fn detect_devices(url: String) -> Result<Vec<Device>, String> {
    gds_core::goxlr::detect_devices(&url).await
}

#[tauri::command]
pub fn open_external(app: AppHandle, url: String) {
    if url.starts_with("https://") {
        let _ = app.opener().open_url(url, None::<&str>);
    }
}

#[tauri::command]
pub fn open_log_folder(app: AppHandle) {
    let _ = app
        .opener()
        .open_path(log_dir().to_string_lossy(), None::<&str>);
}
