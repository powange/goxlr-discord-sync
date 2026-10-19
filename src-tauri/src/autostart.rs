//! Autostart (through tauri-plugin-autostart) and the migration from the Python version.
//!
//! The Run value is named "GoXLR Discord Sync" in both versions. On Windows the
//! plugin writes the path unquoted, so it is rewritten quoted like the Python
//! version did.

use std::path::{Path, PathBuf};

use tauri::AppHandle;
use tauri_plugin_autostart::ManagerExt;

pub const VALUE_NAME: &str = "GoXLR Discord Sync";

pub fn is_enabled(app: &AppHandle) -> bool {
    app.autolaunch().is_enabled().unwrap_or(false)
}

pub fn set_enabled(app: &AppHandle, enabled: bool) -> Result<(), String> {
    let launcher = app.autolaunch();
    if !enabled {
        return launcher.disable().map_err(|e| e.to_string());
    }
    launcher.enable().map_err(|e| e.to_string())?;
    #[cfg(windows)]
    windows::quote_run_value()?;
    Ok(())
}

/// Executable named by a Run command line (quoted or not, with optional arguments).
#[cfg_attr(not(windows), allow(dead_code))]
pub fn command_exe(command: &str) -> Option<PathBuf> {
    let command = command.trim();
    if let Some(rest) = command.strip_prefix('"') {
        return rest
            .split('"')
            .next()
            .filter(|p| !p.is_empty())
            .map(PathBuf::from);
    }
    // Unquoted: the path may contain spaces, so cut after ".exe".
    let lower = command.to_ascii_lowercase();
    match lower.find(".exe") {
        Some(end) => Some(PathBuf::from(&command[..end + 4])),
        None => command.split_whitespace().next().map(PathBuf::from),
    }
}

/// Whether this executable is the installed one: only then may it take over the
/// autostart entry and remove the Python version (never from a test or dev build).
#[cfg_attr(not(windows), allow(dead_code))]
pub fn is_installed_exe(exe: &Path, install_dir: &Path) -> bool {
    exe.parent().is_some_and(|dir| {
        dir.as_os_str()
            .eq_ignore_ascii_case(install_dir.as_os_str())
    })
}

#[cfg(windows)]
pub use windows::migrate_from_python;

#[cfg(not(windows))]
pub fn migrate_from_python(_app: &AppHandle) {}

#[cfg(windows)]
mod windows {
    use std::path::{Path, PathBuf};
    use std::time::Duration;

    use tauri::AppHandle;
    use windows_registry::CURRENT_USER;

    use super::{command_exe, is_installed_exe, VALUE_NAME};

    const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
    const PYTHON_UNINSTALL_KEY: &str =
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GoXLRDiscordSync";
    const PYTHON_EXE: &str = "GoXLR_Discord_Sync.exe";

    fn install_dir() -> Option<PathBuf> {
        std::env::var_os("LOCALAPPDATA").map(|base| PathBuf::from(base).join(VALUE_NAME))
    }

    pub fn quote_run_value() -> Result<(), String> {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        CURRENT_USER
            .create(RUN_KEY)
            .and_then(|key| key.set_string(VALUE_NAME, format!("\"{}\"", exe.display())))
            .map_err(|e| e.to_string())
    }

    pub fn migrate_from_python(app: &AppHandle) {
        let (Ok(exe), Some(dir)) = (std::env::current_exe(), install_dir()) else {
            return;
        };
        if !is_installed_exe(&exe, &dir) {
            return;
        }
        // Autostart still pointing at the Python exe (or at a removed file).
        let registered = CURRENT_USER
            .open(RUN_KEY)
            .and_then(|key| key.get_string(VALUE_NAME))
            .ok();
        if let Some(old) = registered.as_deref().and_then(command_exe) {
            // Not when disabled in the Task Manager: enabling would override that choice.
            if old != exe && super::is_enabled(app) {
                log::info!("Autostart moved from {}", old.display());
                if let Err(e) = super::set_enabled(app, true) {
                    log::error!("Cannot update autostart: {e}");
                }
            }
        }
        // The Python version: its exe in the same folder (or where its Apps entry says).
        let mut folders = vec![dir];
        if let Ok(location) = CURRENT_USER
            .open(PYTHON_UNINSTALL_KEY)
            .and_then(|key| key.get_string("InstallLocation"))
        {
            folders.push(PathBuf::from(location));
        }
        let mut files: Vec<PathBuf> = folders
            .iter()
            .flat_map(|d| [d.join(PYTHON_EXE), d.join(format!("{PYTHON_EXE}.previous"))])
            .filter(|f| f.exists())
            .collect();
        files.sort_by_key(|f| f.to_string_lossy().to_lowercase());
        files.dedup_by_key(|f| f.to_string_lossy().to_lowercase());
        if CURRENT_USER.open(PYTHON_UNINSTALL_KEY).is_ok() {
            match CURRENT_USER.remove_tree(PYTHON_UNINSTALL_KEY) {
                Ok(()) => log::info!("Removed the Apps entry of the Python version"),
                Err(e) => log::error!("Cannot remove the Python Apps entry: {e}"),
            }
        }
        if files.is_empty() {
            return;
        }
        // Both versions running would apply every change twice: the installed
        // version is now this one, so stop the old one (it may also still be exiting).
        std::thread::spawn(move || {
            stop_python_version();
            for file in files {
                remove_with_retry(&file);
            }
        });
    }

    fn stop_python_version() {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let stopped = std::process::Command::new("taskkill")
            .args(["/F", "/IM", PYTHON_EXE])
            .creation_flags(CREATE_NO_WINDOW)
            .output()
            .is_ok_and(|out| out.status.success());
        if stopped {
            log::info!("Stopped the Python version that was still running");
        }
    }

    fn remove_with_retry(file: &Path) {
        for _ in 0..60 {
            match std::fs::remove_file(file) {
                Ok(()) => {
                    log::info!("Removed {}", file.display());
                    return;
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => return,
                Err(_) => std::thread::sleep(Duration::from_millis(500)),
            }
        }
        log::error!("Cannot remove {}", file.display());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_quoted_and_unquoted_commands() {
        let exe = r"C:\Users\a b\AppData\Local\GoXLR Discord Sync\GoXLR_Discord_Sync.exe";
        assert_eq!(command_exe(&format!("\"{exe}\"")), Some(PathBuf::from(exe)));
        assert_eq!(
            command_exe(&format!("\"{exe}\" --flag")),
            Some(PathBuf::from(exe))
        );
        assert_eq!(command_exe(&format!("{exe} ")), Some(PathBuf::from(exe)));
        assert_eq!(
            command_exe(&format!("{exe} --flag")),
            Some(PathBuf::from(exe))
        );
        assert_eq!(
            command_exe("/usr/bin/app --x"),
            Some(PathBuf::from("/usr/bin/app"))
        );
        assert_eq!(command_exe("  "), None);
        assert_eq!(command_exe("\"\""), None);
    }

    #[test]
    fn only_the_installed_exe_migrates() {
        let dir = Path::new("/home/a/GoXLR Discord Sync");
        assert!(is_installed_exe(&dir.join("goxlr-discord-sync"), dir));
        assert!(!is_installed_exe(
            Path::new("/tmp/gds-v3/target/release/app"),
            dir
        ));
    }
}
