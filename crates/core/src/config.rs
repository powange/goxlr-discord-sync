//! Settings in config.json (same file and format as the Python version), file
//! locations, and atomic writes. Secrets are stored elsewhere (see the app).

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// GoXLR mute functions (the "mute_type" of the Cough button and of each fader).
pub const MUTE_TARGETS: [(&str, &str); 5] = [
    ("All", "Muted to everything"),
    ("ToStream", "Muted to Stream"),
    ("ToVoiceChat", "Muted to Voice Chat"),
    ("ToPhones", "Muted to Headphones"),
    ("ToLineOut", "Muted to Line Out"),
];

/// GoXLR channels that can be assigned to a fader.
pub const FADER_CHANNELS: [&str; 11] = [
    "Chat", "Game", "Music", "System", "Console", "LineIn", "Sample", "Mic", "Headphones", "MicMonitor", "LineOut",
];

/// GoXLR buttons that can trigger a Discord action (Cough and fader mutes are already synced).
pub const ACTION_BUTTONS: [(&str, &str); 19] = [
    ("Bleep", "Bleep"), ("EffectFx", "FX"), ("EffectMegaphone", "Megaphone"), ("EffectRobot", "Robot"),
    ("EffectHardTune", "Hard Tune"), ("EffectSelect1", "Effect preset 1"), ("EffectSelect2", "Effect preset 2"),
    ("EffectSelect3", "Effect preset 3"), ("EffectSelect4", "Effect preset 4"), ("EffectSelect5", "Effect preset 5"),
    ("EffectSelect6", "Effect preset 6"), ("SamplerSelectA", "Sampler bank A"), ("SamplerSelectB", "Sampler bank B"),
    ("SamplerSelectC", "Sampler bank C"), ("SamplerTopLeft", "Sampler top left"), ("SamplerTopRight", "Sampler top right"),
    ("SamplerBottomLeft", "Sampler bottom left"), ("SamplerBottomRight", "Sampler bottom right"),
    ("SamplerClear", "Sampler clear"),
];

pub const BUTTON_ACTIONS: [(&str, &str); 3] = [
    ("toggle_mute", "Toggle Discord mute"),
    ("toggle_deafen", "Toggle Discord deafen"),
    ("leave_voice", "Leave the voice channel"),
];

pub const DEFAULT_GOXLR_URL: &str = "ws://localhost:14564/api/websocket";
pub const DEFAULT_OAUTH_PORT: u16 = 9543;

pub fn action_buttons() -> impl Iterator<Item = (&'static str, &'static str)> {
    ACTION_BUTTONS.into_iter()
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Config {
    pub client_id: String,
    pub cough_sync: bool,
    pub chat_sync: bool,
    /// Which effective mute targets count as "muted in Discord" / "deafened in Discord".
    pub mute_triggers: Vec<String>,
    pub deafen_triggers: Vec<String>,
    pub deafen_channel: String,
    /// Empty: first GoXLR found.
    pub mixer_serial: String,
    pub goxlr_url: String,
    pub bidirectional: bool,
    pub notifications: bool,
    pub check_updates: bool,
    pub oauth_port: u16,
    /// Discord action -> GoXLR button that triggers it, e.g. {"leave_voice": "SamplerClear"}.
    pub button_actions: BTreeMap<String, String>,
    /// auto | en | fr
    pub language: String,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            client_id: String::new(),
            cough_sync: true,
            chat_sync: true,
            mute_triggers: vec!["All".into(), "ToVoiceChat".into()],
            deafen_triggers: vec!["All".into(), "ToPhones".into()],
            deafen_channel: "Chat".into(),
            mixer_serial: String::new(),
            goxlr_url: DEFAULT_GOXLR_URL.into(),
            bidirectional: false,
            notifications: true,
            check_updates: true,
            oauth_port: DEFAULT_OAUTH_PORT,
            button_actions: BTreeMap::new(),
            language: "auto".into(),
        }
    }
}

impl Config {
    pub fn redirect_uri(&self) -> String {
        format!("http://127.0.0.1:{}/callback", self.oauth_port)
    }

    /// Lenient reading: unknown keys and invalid values keep their defaults.
    pub fn from_value(data: &Value) -> Config {
        let mut cfg = Config::default();
        let Some(map) = data.as_object() else { return cfg };
        let text = |k: &str| map.get(k).and_then(Value::as_str).map(|s| s.trim().to_string());
        let flag = |k: &str, d: bool| map.get(k).and_then(Value::as_bool).unwrap_or(d);
        let targets = |k: &str, d: &Vec<String>| match map.get(k).and_then(Value::as_array) {
            Some(list) => list.iter().filter_map(Value::as_str)
                .filter(|t| MUTE_TARGETS.iter().any(|(m, _)| m == t)).map(str::to_string).collect(),
            None => d.clone(),
        };
        if let Some(v) = text("client_id") { cfg.client_id = v; }
        if let Some(v) = text("mixer_serial") { cfg.mixer_serial = v; }
        if let Some(v) = text("goxlr_url").filter(|v| !v.is_empty()) { cfg.goxlr_url = v; }
        if let Some(v) = text("deafen_channel").filter(|v| !v.is_empty()) { cfg.deafen_channel = v; }
        if let Some(v) = text("language").filter(|v| matches!(v.as_str(), "auto" | "en" | "fr")) { cfg.language = v; }
        cfg.cough_sync = flag("cough_sync", cfg.cough_sync);
        cfg.chat_sync = flag("chat_sync", cfg.chat_sync);
        cfg.bidirectional = flag("bidirectional", cfg.bidirectional);
        cfg.notifications = flag("notifications", cfg.notifications);
        cfg.check_updates = flag("check_updates", cfg.check_updates);
        cfg.mute_triggers = targets("mute_triggers", &cfg.mute_triggers);
        cfg.deafen_triggers = targets("deafen_triggers", &cfg.deafen_triggers);
        if let Some(port) = map.get("oauth_port").and_then(Value::as_u64).filter(|p| (1..=65535).contains(p)) {
            cfg.oauth_port = port as u16;
        }
        if let Some(actions) = map.get("button_actions").and_then(Value::as_object) {
            cfg.button_actions = actions.iter()
                .filter(|(a, _)| BUTTON_ACTIONS.iter().any(|(k, _)| k == a))
                .filter_map(|(a, b)| b.as_str().filter(|b| action_buttons().any(|(k, _)| k == *b)).map(|b| (a.clone(), b.to_string())))
                .collect();
        }
        cfg
    }

    pub fn to_value(&self) -> Value {
        serde_json::to_value(self).expect("serializable config")
    }

    pub fn load(path: &Path) -> Config {
        std::fs::read_to_string(path).ok()
            .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
            .map(|v| Config::from_value(&v))
            .unwrap_or_default()
    }

    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        write_json(path, &self.to_value())
    }
}

/// Per-user folder for config.json, state.json and logs (shared with the Python version).
pub fn data_dir() -> PathBuf {
    let dir = if let Ok(home) = std::env::var("GOXLR_DISCORD_SYNC_HOME") {
        PathBuf::from(home)
    } else if cfg!(windows) {
        PathBuf::from(std::env::var("APPDATA").unwrap_or_else(|_| ".".into())).join("GoXLR Discord Sync")
    } else if cfg!(target_os = "macos") {
        PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("Library/Application Support/GoXLR Discord Sync")
    } else {
        let base = std::env::var("XDG_CONFIG_HOME").ok().filter(|v| !v.is_empty())
            .unwrap_or_else(|| format!("{}/.config", std::env::var("HOME").unwrap_or_default()));
        PathBuf::from(base).join("goxlr-discord-sync")
    };
    let _ = std::fs::create_dir_all(&dir);
    dir
}

pub fn config_file() -> PathBuf {
    data_dir().join("config.json")
}

pub fn state_file() -> PathBuf {
    data_dir().join("state.json")
}

pub fn log_dir() -> PathBuf {
    let dir = data_dir().join("logs");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

pub fn read_json(path: &Path) -> Option<serde_json::Map<String, Value>> {
    let raw = std::fs::read_to_string(path).ok()?;
    match serde_json::from_str::<Value>(&raw).ok()? {
        Value::Object(map) => Some(map),
        _ => None,
    }
}

pub fn write_json(path: &Path, value: &Value) -> std::io::Result<()> {
    let text = serde_json::to_string_pretty(value).expect("serializable");
    atomic_write(path, text.as_bytes())
}

/// Write through a temporary file; the final rename is retried while Windows
/// reports the file as busy (antivirus, indexer, the settings window reading it).
pub fn atomic_write(path: &Path, data: &[u8]) -> std::io::Result<()> {
    write_then_rename(path, data, false)
}

/// Same, readable by the owner only on Unix (secrets).
pub fn atomic_write_private(path: &Path, data: &[u8]) -> std::io::Result<()> {
    write_then_rename(path, data, true)
}

fn write_then_rename(path: &Path, data: &[u8], private: bool) -> std::io::Result<()> {
    use std::io::Write;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension(format!("{}.tmp", std::process::id()));
    let mut options = std::fs::File::options();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    if private {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    #[cfg(not(unix))]
    let _ = private;
    let mut file = options.open(&tmp)?;
    file.write_all(data)?;
    file.sync_all()?;
    drop(file);
    let mut last = None;
    for _ in 0..20 {
        match std::fs::rename(&tmp, path) {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
                last = Some(e);
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Err(e) => {
                let _ = std::fs::remove_file(&tmp);
                return Err(e);
            }
        }
    }
    let _ = std::fs::remove_file(&tmp);
    Err(last.unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn roundtrip_and_validation() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.json");
        let mut cfg = Config { client_id: "42".into(), oauth_port: 9000, ..Config::default() };
        cfg.mute_triggers = vec!["All".into()];
        cfg.save(&path).unwrap();
        assert_eq!(Config::load(&path), cfg);

        let loaded = Config::from_value(&json!({
            "client_id": " 7 ", "cough_sync": "yes", "oauth_port": 99999, "mute_triggers": ["All", "Bogus"],
            "goxlr_url": "", "unknown": 1, "language": "de",
            "button_actions": {"toggle_mute": "Bleep", "explode": "Bleep", "leave_voice": "Cough"}}));
        assert_eq!(loaded.client_id, "7");
        assert!(loaded.cough_sync); // invalid type ignored
        assert_eq!(loaded.oauth_port, DEFAULT_OAUTH_PORT);
        assert_eq!(loaded.mute_triggers, vec!["All".to_string()]);
        assert_eq!(loaded.goxlr_url, DEFAULT_GOXLR_URL);
        assert_eq!(loaded.language, "auto");
        assert_eq!(loaded.button_actions.clone().into_iter().collect::<Vec<_>>(), vec![("toggle_mute".into(), "Bleep".into())]);
        assert_eq!(loaded.redirect_uri(), "http://127.0.0.1:9543/callback");
    }

    #[test]
    fn reads_the_python_config_file() {
        // Written by Config.save() of the Python 2.5 version.
        let python = json!({"client_id": "1462314594138718221", "cough_sync": true, "chat_sync": true,
            "mute_triggers": ["All", "ToVoiceChat"], "deafen_triggers": ["All", "ToPhones"], "deafen_channel": "Chat",
            "mixer_serial": "", "goxlr_url": "ws://localhost:14564/api/websocket", "bidirectional": true,
            "notifications": true, "check_updates": true, "oauth_port": 9543,
            "button_actions": {"leave_voice": "SamplerClear"}, "language": "fr"});
        let cfg = Config::from_value(&python);
        assert_eq!(cfg.to_value(), python); // same keys, same values: both versions can share the file
    }

    #[test]
    fn corrupted_file_gives_defaults() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.json");
        std::fs::write(&path, "{not json").unwrap();
        assert_eq!(Config::load(&path), Config::default());
    }
}
