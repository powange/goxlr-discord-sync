//! System tray icon and menu (same entries as the Python version).

use gds_core::config::{config_file, log_dir};
use gds_core::engine::{Glyph, IconColor, IconSpec};
use gds_core::i18n::tr;
use tauri::image::Image;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_opener::OpenerExt;

use crate::host::{Cmd, Host};

const TRAY: &str = "main";
pub const REPO_URL: &str = "https://github.com/powange/goxlr-discord-sync";

fn icon(spec: IconSpec) -> Image<'static> {
    macro_rules! png {
        ($name:literal) => {
            include_bytes!(concat!("../icons/tray/", $name, ".png")) as &[u8]
        };
    }
    let bytes = match (spec.color, spec.glyph, spec.slashed) {
        (IconColor::Live, Glyph::Mic, false) => png!("live-mic"),
        (IconColor::Live, Glyph::Mic, true) => png!("live-mic-slashed"),
        (IconColor::Live, Glyph::Headphones, false) => png!("live-headphones"),
        (IconColor::Live, Glyph::Headphones, true) => png!("live-headphones-slashed"),
        (IconColor::Muted, Glyph::Mic, false) => png!("muted-mic"),
        (IconColor::Muted, Glyph::Mic, true) => png!("muted-mic-slashed"),
        (IconColor::Muted, Glyph::Headphones, false) => png!("muted-headphones"),
        (IconColor::Muted, Glyph::Headphones, true) => png!("muted-headphones-slashed"),
        (IconColor::Warning, Glyph::Mic, false) => png!("warning-mic"),
        (IconColor::Warning, Glyph::Mic, true) => png!("warning-mic-slashed"),
        (IconColor::Warning, Glyph::Headphones, false) => png!("warning-headphones"),
        (IconColor::Warning, Glyph::Headphones, true) => png!("warning-headphones-slashed"),
        (IconColor::Idle, Glyph::Mic, false) => png!("idle-mic"),
        (IconColor::Idle, Glyph::Mic, true) => png!("idle-mic-slashed"),
        (IconColor::Idle, Glyph::Headphones, false) => png!("idle-headphones"),
        (IconColor::Idle, Glyph::Headphones, true) => png!("idle-headphones-slashed"),
    };
    Image::from_bytes(bytes).expect("bundled PNG")
}

fn autostart_label() -> String {
    tr(
        if cfg!(windows) {
            "Start with Windows"
        } else {
            "Start on login"
        },
        &[],
    )
}

fn build_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let shared = app.state::<Host>().snapshot();
    let cfg = &shared.cfg;
    let autostart = app.autolaunch().is_enabled().unwrap_or(false);
    let item = |id: &str, text: String, enabled: bool| {
        MenuItem::with_id(app, id, text, enabled, None::<&str>)
    };
    let check = |id: &str, text: String, checked: bool| {
        CheckMenuItem::with_id(app, id, text, true, checked, None::<&str>)
    };
    let separator = || PredefinedMenuItem::separator(app);
    let menu = Menu::new(app)?;
    menu.append(&item("summary", shared.summary.clone(), false)?)?;
    menu.append(&separator()?)?;
    menu.append(&check(
        "cough_sync",
        tr("Cough button → Discord mute", &[]),
        cfg.cough_sync,
    )?)?;
    menu.append(&check(
        "chat_sync",
        tr(
            "{channel} mute → Discord deafen",
            &[("channel", &cfg.deafen_channel)],
        ),
        cfg.chat_sync,
    )?)?;
    menu.append(&check(
        "bidirectional",
        tr("Two-way sync (Discord → GoXLR)", &[]),
        cfg.bidirectional,
    )?)?;
    menu.append(&separator()?)?;
    menu.append(&item("status", tr("Status", &[]), true)?)?;
    menu.append(&item("settings", tr("Settings…", &[]), true)?)?;
    menu.append(&check("autostart", autostart_label(), autostart)?)?;
    menu.append(&item(
        "reauthorize",
        tr("Authorize Discord again", &[]),
        true,
    )?)?;
    menu.append(&item("log", tr("Open log", &[]), true)?)?;
    if let Some(version) = &shared.update {
        menu.append(&item(
            "install_update",
            tr("Install update v{version}…", &[("version", version)]),
            true,
        )?)?;
    }
    menu.append(&item("check_updates", tr("Check for updates", &[]), true)?)?;
    menu.append(&item(
        "about",
        tr(
            "About (v{version})",
            &[("version", env!("CARGO_PKG_VERSION"))],
        ),
        true,
    )?)?;
    menu.append(&separator()?)?;
    menu.append(&item("quit", tr("Quit", &[]), true)?)?;
    Ok(menu)
}

pub fn create(app: &AppHandle) -> tauri::Result<()> {
    let shared = app.state::<Host>().snapshot();
    TrayIconBuilder::with_id(TRAY)
        .icon(icon(shared.icon))
        .tooltip("GoXLR Discord Sync")
        .menu(&build_menu(app)?)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| on_menu(app, event.id().as_ref()))
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_status(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

/// Icon, tooltip and menu from the current state (main thread).
pub fn refresh(app: &AppHandle) {
    let Some(tray) = app.tray_by_id(TRAY) else {
        return;
    };
    let shared = app.state::<Host>().snapshot();
    let _ = tray.set_icon(Some(icon(shared.icon)));
    // 127 characters: Windows tooltip limit.
    let tooltip: String = format!("GoXLR Discord Sync - {}", shared.summary)
        .chars()
        .take(127)
        .collect();
    let _ = tray.set_tooltip(Some(tooltip));
    // Rebuild the menu only when something it shows changed (no churn on every update).
    let cfg = &shared.cfg;
    let signature = format!(
        "{}|{}|{}|{}|{}|{:?}|{}|{}",
        shared.summary,
        cfg.cough_sync,
        cfg.chat_sync,
        cfg.bidirectional,
        cfg.deafen_channel,
        shared.update,
        gds_core::i18n::current(),
        app.autolaunch().is_enabled().unwrap_or(false)
    );
    static LAST: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());
    let mut last = LAST.lock().unwrap();
    if *last == signature {
        return;
    }
    match build_menu(app) {
        Ok(menu) => {
            let _ = tray.set_menu(Some(menu));
            *last = signature;
        }
        Err(e) => log::warn!("Cannot rebuild the tray menu: {e}"),
    }
}

fn show_status(app: &AppHandle) {
    let details = app.state::<Host>().snapshot().details;
    crate::host::show_notification(app, "GoXLR Discord Sync", &details);
}

fn toggle(app: &AppHandle, field: &str) {
    let host = app.state::<Host>();
    let mut cfg = host.snapshot().cfg;
    match field {
        "cough_sync" => cfg.cough_sync = !cfg.cough_sync,
        "chat_sync" => cfg.chat_sync = !cfg.chat_sync,
        _ => cfg.bidirectional = !cfg.bidirectional,
    }
    if let Err(e) = cfg.save(&config_file()) {
        log::error!("Cannot save the settings: {e}");
    }
    log::info!("{field} changed");
    host.send(Cmd::ApplyConfig {
        cfg,
        credentials_changed: false,
    });
}

fn on_menu(app: &AppHandle, id: &str) {
    match id {
        "cough_sync" | "chat_sync" | "bidirectional" => toggle(app, id),
        "status" => show_status(app),
        "settings" => crate::settings::open(app),
        "autostart" => {
            let launcher = app.autolaunch();
            let result = if launcher.is_enabled().unwrap_or(false) {
                launcher.disable()
            } else {
                launcher.enable()
            };
            if let Err(e) = result {
                log::error!("Cannot change autostart: {e}");
                crate::host::show_notification(
                    app,
                    &tr("Autostart", &[]),
                    &tr(
                        "Could not change the setting: {error}",
                        &[("error", &e.to_string())],
                    ),
                );
            }
            refresh(app);
        }
        "reauthorize" => app.state::<Host>().send(Cmd::Reauthorize),
        "log" => {
            let _ = app
                .opener()
                .open_path(log_dir().join("app.log").to_string_lossy(), None::<&str>);
        }
        "install_update" => crate::updates::install(app.clone()),
        "check_updates" => crate::updates::check_now(app.clone()),
        "about" => {
            let _ = app.opener().open_url(REPO_URL, None::<&str>);
        }
        "quit" => {
            log::info!("Quit requested");
            app.exit(0);
        }
        _ => {}
    }
}
