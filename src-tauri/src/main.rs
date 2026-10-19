//! GoXLR Discord Sync 3: tray application (Tauri 2) around the gds-core engine.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod host;
mod settings;
mod tray;
mod updates;

use gds_core::config::{config_file, log_dir, Config};
use gds_core::i18n;
use tauri::{Manager, RunEvent};
use tauri_plugin_log::{RotationStrategy, Target, TargetKind, TimezoneStrategy};

fn autostart_plugin<R: tauri::Runtime>() -> tauri::plugin::TauriPlugin<R> {
    let builder = tauri_plugin_autostart::Builder::new().app_name("GoXLR Discord Sync");
    #[cfg(target_os = "macos")]
    let builder = builder.macos_launcher(tauri_plugin_autostart::MacosLauncher::LaunchAgent);
    builder.build()
}

fn main() {
    let cfg = Config::load(&config_file());
    i18n::set_language(&cfg.language);

    let app = tauri::Builder::default()
        // First plugin: a second launch opens the settings of the running instance.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            settings::open(app)
        }))
        .plugin(
            tauri_plugin_log::Builder::new()
                .targets([
                    Target::new(TargetKind::Folder {
                        path: log_dir(),
                        file_name: Some("app".into()),
                    }),
                    Target::new(TargetKind::Stdout),
                ])
                .level(log::LevelFilter::Info)
                .max_file_size(1_000_000)
                .rotation_strategy(RotationStrategy::KeepSome(3))
                .timezone_strategy(TimezoneStrategy::UseLocal)
                .build(),
        )
        // Same Run value name as the Python version: the migration simply updates its path.
        .plugin(autostart_plugin())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(updates::PendingUpdate::default())
        .invoke_handler(tauri::generate_handler![
            settings::get_settings,
            settings::save_settings,
            settings::check_credentials,
            settings::detect_devices,
            settings::open_external,
            settings::open_log_folder,
        ])
        .setup(move |app| {
            log::info!("GoXLR Discord Sync {} starting", env!("CARGO_PKG_VERSION"));
            let handle = app.handle().clone();
            app.manage(host::start(handle.clone(), cfg.clone()));
            tray::create(&handle)?;
            updates::announce_new_version(&handle);
            updates::start(handle.clone());
            let configured =
                !cfg.client_id.is_empty() && !host::secrets().client_secret().is_empty();
            if !configured {
                log::warn!("Discord application not configured");
                settings::open(&handle);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("GoXLR Discord Sync could not start");

    app.run(|_app, event| {
        // Closing the settings window must not quit the tray application.
        if let RunEvent::ExitRequested {
            api, code: None, ..
        } = event
        {
            api.prevent_exit();
        }
    });
}
