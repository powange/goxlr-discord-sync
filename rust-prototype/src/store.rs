//! Read the configuration and Discord token written by the Python application (never writes).

use serde_json::Value;
use std::path::PathBuf;

pub struct Settings {
    pub client_id: String,
    pub goxlr_url: String,
    pub mute_triggers: Vec<String>,
    pub deafen_triggers: Vec<String>,
    pub deafen_channel: String,
    pub access_token: String,
}

fn data_dir() -> PathBuf {
    if cfg!(windows) {
        PathBuf::from(std::env::var("APPDATA").unwrap_or_default()).join("GoXLR Discord Sync")
    } else {
        let base = std::env::var("XDG_CONFIG_HOME")
            .unwrap_or_else(|_| format!("{}/.config", std::env::var("HOME").unwrap_or_default()));
        PathBuf::from(base).join("goxlr-discord-sync")
    }
}

fn strings(v: &Value, key: &str, default: &[&str]) -> Vec<String> {
    v.get(key).and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_else(|| default.iter().map(|s| s.to_string()).collect())
}

pub fn load() -> Result<Settings, String> {
    let dir = data_dir();
    let raw = std::fs::read_to_string(dir.join("config.json")).map_err(|e| format!("config.json: {e}"))?;
    let cfg: Value = serde_json::from_str(&raw).map_err(|e| format!("config.json: {e}"))?;
    let secrets = read_secrets(&std::fs::read(dir.join("secrets.dat")).map_err(|e| format!("secrets.dat: {e}"))?)?;
    let token = secrets.get("token").ok_or("no Discord token yet: authorize with the Python app first")?;
    let expires_at = token.get("expires_at").and_then(Value::as_f64).unwrap_or(0.0);
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    if expires_at - 300.0 < now {
        // The prototype never refreshes: that would rotate the token the Python app relies on.
        return Err("Discord token expired: start the Python app once so it refreshes it".into());
    }
    Ok(Settings {
        client_id: cfg.get("client_id").and_then(Value::as_str).unwrap_or_default().to_string(),
        goxlr_url: cfg.get("goxlr_url").and_then(Value::as_str)
            .unwrap_or("ws://localhost:14564/api/websocket").to_string(),
        mute_triggers: strings(&cfg, "mute_triggers", &["All", "ToVoiceChat"]),
        deafen_triggers: strings(&cfg, "deafen_triggers", &["All", "ToPhones"]),
        deafen_channel: cfg.get("deafen_channel").and_then(Value::as_str).unwrap_or("Chat").to_string(),
        access_token: token.get("access_token").and_then(Value::as_str).unwrap_or_default().to_string(),
    })
}

fn read_secrets(blob: &[u8]) -> Result<Value, String> {
    let json = if let Some(rest) = blob.strip_prefix(b"DPAPI1") {
        unprotect(rest)?
    } else if let Some(rest) = blob.strip_prefix(b"PLAIN1") {
        rest.to_vec()
    } else {
        return Err("unknown secrets.dat format".into());
    };
    serde_json::from_slice(&json).map_err(|e| format!("secrets.dat: {e}"))
}

#[cfg(windows)]
fn unprotect(data: &[u8]) -> Result<Vec<u8>, String> {
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{CryptUnprotectData, CRYPT_INTEGER_BLOB};
    // Same entropy and flags as goxlr_discord_sync/secure_store.py.
    let mut entropy_bytes = b"goxlr-discord-sync".to_vec();
    let mut input = data.to_vec();
    let data_in = CRYPT_INTEGER_BLOB { cbData: input.len() as u32, pbData: input.as_mut_ptr() };
    let entropy = CRYPT_INTEGER_BLOB { cbData: entropy_bytes.len() as u32, pbData: entropy_bytes.as_mut_ptr() };
    let mut out = CRYPT_INTEGER_BLOB { cbData: 0, pbData: std::ptr::null_mut() };
    let ok = unsafe {
        CryptUnprotectData(&data_in, std::ptr::null_mut(), &entropy, std::ptr::null(), std::ptr::null(), 1, &mut out)
    };
    if ok == 0 {
        return Err("DPAPI could not decrypt secrets.dat".into());
    }
    let plain = unsafe { std::slice::from_raw_parts(out.pbData, out.cbData as usize).to_vec() };
    unsafe { LocalFree(out.pbData as _) };
    Ok(plain)
}

#[cfg(not(windows))]
fn unprotect(_data: &[u8]) -> Result<Vec<u8>, String> {
    Err("DPAPI data can only be read on Windows".into())
}
