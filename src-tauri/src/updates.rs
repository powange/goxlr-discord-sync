//! Updates through the Tauri updater (latest.json on GitHub releases, signed).
//! Nothing is installed without the user's confirmation; an automatic notice
//! found during a voice call waits for the end of the call.

use gds_core::config::{read_json, state_file, write_json};
use gds_core::i18n::tr;
use serde_json::{json, Value};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::host::{show_notification, Host};

const CHECK_INTERVAL: f64 = 24.0 * 3600.0;
const APP: &str = "GoXLR Discord Sync";

/// The update found by the last check, kept for "Install update".
#[derive(Default)]
pub struct PendingUpdate(pub Mutex<Option<Update>>);

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn state() -> serde_json::Map<String, Value> {
    read_json(&state_file()).unwrap_or_default()
}

fn save_state(state: serde_json::Map<String, Value>) {
    if let Err(e) = write_json(&state_file(), &Value::Object(state)) {
        log::warn!("Cannot save state.json: {e}");
    }
}

/// Tell the user once that an update was installed (same state.json key as Python).
pub fn announce_new_version(app: &AppHandle) {
    let mut st = state();
    let current = env!("CARGO_PKG_VERSION");
    let previous = st
        .get("last_run_version")
        .and_then(Value::as_str)
        .map(str::to_string);
    if previous.as_deref() != Some(current) {
        if previous.is_some() {
            show_notification(
                app,
                &tr("Update installed", &[]),
                &tr(
                    "{app} is now version {version}.",
                    &[("app", APP), ("version", current)],
                ),
            );
        }
        st.insert("last_run_version".into(), json!(current));
        save_state(st);
    }
}

pub fn notify_available(app: &AppHandle, version: &str) {
    let action = tr("Install update", &[]);
    show_notification(
        app,
        &tr("Update available", &[]),
        &tr(
            "Version {version} is available. Choose “{action}” in the tray menu.",
            &[("version", version), ("action", &action)],
        ),
    );
}

pub fn start(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(20)).await;
        loop {
            let enabled = app.state::<Host>().snapshot().cfg.check_updates;
            let last = state()
                .get("last_update_check")
                .and_then(Value::as_f64)
                .unwrap_or(0.0);
            if enabled && now() - last >= CHECK_INTERVAL {
                check(&app, false).await;
            }
            tokio::time::sleep(Duration::from_secs(3600)).await;
        }
    });
}

pub fn check_now(app: AppHandle) {
    tauri::async_runtime::spawn(async move { check(&app, true).await });
}

async fn check(app: &AppHandle, manual: bool) {
    let result = match app.updater() {
        Ok(updater) => updater.check().await,
        Err(e) => Err(e),
    };
    let mut st = state();
    st.insert("last_update_check".into(), json!(now()));
    match result {
        Err(e) => {
            log::warn!("Update check failed: {e}");
            save_state(st);
            if manual {
                show_notification(
                    app,
                    &tr("Update check failed", &[]),
                    &tr("GitHub could not be reached. Try again later.", &[]),
                );
            }
        }
        Ok(None) => {
            save_state(st);
            set_available(app, None);
            if manual {
                show_notification(
                    app,
                    &tr("No update available", &[]),
                    &tr(
                        "You're running the latest version ({version}).",
                        &[("version", env!("CARGO_PKG_VERSION"))],
                    ),
                );
            }
        }
        Ok(Some(update)) => {
            let version = update.version.clone();
            let first_notice =
                st.get("notified_version").and_then(Value::as_str) != Some(version.as_str());
            st.insert("notified_version".into(), json!(version));
            save_state(st);
            *app.state::<PendingUpdate>().0.lock().unwrap() = Some(update);
            set_available(app, Some(version.clone()));
            if manual {
                install(app.clone()); // the confirmation dialog is the answer
            } else if first_notice {
                let host = app.state::<Host>();
                if host.in_call() {
                    log::info!("Update {version} found during a call: notification postponed");
                    host.shared.lock().unwrap().deferred_update_notice = Some(version);
                } else {
                    notify_available(app, &version);
                }
            }
        }
    }
}

fn set_available(app: &AppHandle, version: Option<String>) {
    app.state::<Host>().shared.lock().unwrap().update = version;
    let tray_app = app.clone();
    let _ = app.run_on_main_thread(move || crate::tray::refresh(&tray_app));
}

/// Ask for confirmation, then download, install and restart.
pub fn install(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        let Some(update) = app.state::<PendingUpdate>().0.lock().unwrap().clone() else {
            return;
        };
        let notes = update.body.clone().unwrap_or_default();
        let notes: String = notes.chars().take(600).collect();
        let mut question = tr(
            "{app} {version} is available (you have {current}).",
            &[
                ("app", APP),
                ("version", &update.version),
                ("current", env!("CARGO_PKG_VERSION")),
            ],
        );
        question.push_str("\n\n");
        if !notes.trim().is_empty() {
            question.push_str(notes.trim());
            question.push_str("\n\n");
        }
        if app.state::<Host>().in_call() {
            question.push_str(&tr(
                "You are in a voice call: mute sync stops for a few seconds during the update.",
                &[],
            ));
            question.push_str("\n\n");
        }
        question.push_str(&tr(
            "Install it now? The application closes for a few seconds and starts again on its own.",
            &[],
        ));
        let confirmed = app
            .dialog()
            .message(question)
            .title(tr("{app} update", &[("app", APP)]))
            .buttons(MessageDialogButtons::OkCancelCustom(
                tr("Install update", &[]),
                tr("Cancel", &[]),
            ))
            .blocking_show();
        if !confirmed {
            log::info!("Update to {} postponed by the user", update.version);
            return;
        }
        show_notification(
            &app,
            &tr("Downloading update", &[]),
            &tr(
                "Downloading version {version}…",
                &[("version", &update.version)],
            ),
        );
        match update.download_and_install(|_, _| {}, || {}).await {
            Ok(()) => {
                log::info!("Update to {} installed, restarting", update.version);
                app.restart();
            }
            Err(e) => {
                log::error!("Update failed: {e}");
                let retry = app
                    .dialog()
                    .message(format!(
                        "{}\n\n{e}\n\n{}",
                        tr("The update could not be installed:", &[]),
                        tr("Open the download page instead?", &[])
                    ))
                    .title(tr("{app} update", &[("app", APP)]))
                    .buttons(MessageDialogButtons::YesNo)
                    .blocking_show();
                if retry {
                    use tauri_plugin_opener::OpenerExt;
                    let _ = app.opener().open_url(
                        format!("{}/releases/latest", crate::tray::REPO_URL),
                        None::<&str>,
                    );
                }
            }
        }
    });
}
