//! secrets.dat: the Discord client secret and OAuth tokens, in the same format
//! as the Python version (so both read the same file during the migration):
//! "DPAPI1" + DPAPI blob on Windows (bound to the Windows user), "PLAIN1" + JSON
//! with 0600 permissions elsewhere. Read-modify-write cycles take secrets.lock,
//! which also excludes the Python version (msvcrt / flock locks).

use serde_json::{Map, Value};
use std::fs::File;
use std::io;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use crate::auth::TokenStore;

const DPAPI_MAGIC: &[u8] = b"DPAPI1";
const PLAIN_MAGIC: &[u8] = b"PLAIN1";

pub struct SecretsFile {
    dir: PathBuf,
}

impl SecretsFile {
    pub fn new(dir: PathBuf) -> SecretsFile {
        SecretsFile { dir }
    }

    pub fn path(&self) -> PathBuf {
        self.dir.join("secrets.dat")
    }

    /// Err when the file exists but cannot be read: never overwrite it blindly.
    fn load_strict(&self) -> io::Result<Map<String, Value>> {
        let blob = match std::fs::read(self.path()) {
            Ok(blob) => blob,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(Map::new()),
            Err(e) => return Err(e),
        };
        decode(&blob).map_err(|e| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("Secrets file is unreadable: {e}"),
            )
        })
    }

    pub fn load(&self) -> Map<String, Value> {
        self.load_strict().unwrap_or_else(|e| {
            log::warn!("{e}");
            Map::new()
        })
    }

    fn save(&self, data: &Map<String, Value>) -> io::Result<()> {
        let blob = encode(data)?;
        crate::config::atomic_write_private(&self.path(), &blob)
    }

    fn locked<T>(&self, work: impl FnOnce() -> io::Result<T>) -> io::Result<T> {
        std::fs::create_dir_all(&self.dir)?;
        let lock = File::options()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(self.dir.join("secrets.lock"))?;
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            match lock.try_lock() {
                Ok(()) => break,
                Err(std::fs::TryLockError::WouldBlock) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(20))
                }
                Err(std::fs::TryLockError::WouldBlock) => {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "secrets.dat is locked by another process",
                    ));
                }
                Err(std::fs::TryLockError::Error(e)) => return Err(e),
            }
        }
        let result = work();
        let _ = lock.unlock();
        result
    }

    pub fn client_secret(&self) -> String {
        self.load()
            .get("client_secret")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    }

    pub fn set_client_secret(&self, secret: &str) -> io::Result<()> {
        self.locked(|| {
            // A new secret replaces an unreadable file on purpose.
            let mut data = self.load_strict().unwrap_or_default();
            if data.get("client_secret").and_then(Value::as_str) != Some(secret) {
                data.remove("token"); // tokens issued for the old secret are useless
            }
            data.insert("client_secret".into(), Value::String(secret.to_string()));
            self.save(&data)
        })
    }

    pub fn clear_all(&self) -> io::Result<()> {
        match std::fs::remove_file(self.path()) {
            Err(e) if e.kind() != io::ErrorKind::NotFound => Err(e),
            _ => Ok(()),
        }
    }
}

impl TokenStore for SecretsFile {
    fn get_token(&self) -> Option<Value> {
        self.load().get("token").filter(|t| t.is_object()).cloned()
    }

    fn set_token(&self, token: Option<Value>) -> io::Result<()> {
        self.locked(|| {
            let mut data = self.load_strict()?; // never wipe the client secret
            match token {
                Some(token) => data.insert("token".into(), token),
                None => data.remove("token"),
            };
            self.save(&data)
        })
    }
}

fn encode(data: &Map<String, Value>) -> io::Result<Vec<u8>> {
    let raw = serde_json::to_vec(data).expect("serializable");
    if cfg!(windows) {
        Ok([DPAPI_MAGIC, &dpapi::protect(&raw)?].concat())
    } else {
        Ok([PLAIN_MAGIC, &raw].concat())
    }
}

fn decode(blob: &[u8]) -> Result<Map<String, Value>, String> {
    let raw = if let Some(rest) = blob.strip_prefix(DPAPI_MAGIC) {
        dpapi::unprotect(rest).map_err(|e| e.to_string())?
    } else if let Some(rest) = blob.strip_prefix(PLAIN_MAGIC) {
        rest.to_vec()
    } else {
        return Err("unknown secrets file format".into());
    };
    match serde_json::from_slice::<Value>(&raw).map_err(|e| e.to_string())? {
        Value::Object(map) => Ok(map),
        _ => Ok(Map::new()),
    }
}

#[cfg(windows)]
mod dpapi {
    use std::io;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CryptUnprotectData, CRYPT_INTEGER_BLOB,
    };

    /// Same entropy and flags as goxlr_discord_sync/secure_store.py.
    const ENTROPY: &[u8] = b"goxlr-discord-sync";
    const CRYPTPROTECT_UI_FORBIDDEN: u32 = 1;

    fn blob(data: &mut [u8]) -> CRYPT_INTEGER_BLOB {
        CRYPT_INTEGER_BLOB {
            cbData: data.len() as u32,
            pbData: data.as_mut_ptr(),
        }
    }

    fn call(protect: bool, data: &[u8]) -> io::Result<Vec<u8>> {
        let (mut input, mut entropy) = (data.to_vec(), ENTROPY.to_vec());
        let (data_in, entropy) = (blob(&mut input), blob(&mut entropy));
        let mut out = CRYPT_INTEGER_BLOB {
            cbData: 0,
            pbData: std::ptr::null_mut(),
        };
        let ok = unsafe {
            if protect {
                CryptProtectData(
                    &data_in,
                    std::ptr::null(),
                    &entropy,
                    std::ptr::null(),
                    std::ptr::null(),
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut out,
                )
            } else {
                CryptUnprotectData(
                    &data_in,
                    std::ptr::null_mut(),
                    &entropy,
                    std::ptr::null(),
                    std::ptr::null(),
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut out,
                )
            }
        };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        let result =
            unsafe { std::slice::from_raw_parts(out.pbData, out.cbData as usize).to_vec() };
        unsafe { LocalFree(out.pbData as _) };
        Ok(result)
    }

    pub fn protect(data: &[u8]) -> io::Result<Vec<u8>> {
        call(true, data)
    }

    pub fn unprotect(data: &[u8]) -> io::Result<Vec<u8>> {
        call(false, data)
    }
}

#[cfg(not(windows))]
mod dpapi {
    use std::io;

    pub fn protect(_: &[u8]) -> io::Result<Vec<u8>> {
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "DPAPI is Windows only",
        ))
    }

    pub fn unprotect(_: &[u8]) -> io::Result<Vec<u8>> {
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "DPAPI data can only be read on Windows",
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn roundtrip_secret_and_token() {
        let dir = tempfile::tempdir().unwrap();
        let store = SecretsFile::new(dir.path().to_path_buf());
        store.set_client_secret("abc").unwrap();
        store.set_token(Some(json!({"access_token": "t"}))).unwrap();
        assert_eq!(store.client_secret(), "abc");
        assert_eq!(store.get_token(), Some(json!({"access_token": "t"})));
        store.set_client_secret("other").unwrap(); // new secret: old token is useless
        assert_eq!(store.get_token(), None);
        let raw = std::fs::read(store.path()).unwrap();
        assert!(!raw.windows(5).any(|w| w == b"other") || !cfg!(windows));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(store.path())
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o600
            );
        }
    }

    #[test]
    fn reads_the_python_plain_format_and_never_overwrites_unreadable_files() {
        let dir = tempfile::tempdir().unwrap();
        let store = SecretsFile::new(dir.path().to_path_buf());
        if !cfg!(windows) {
            // Written by secure_store.save() of the Python version on Linux.
            std::fs::write(
                store.path(),
                br#"PLAIN1{"client_secret": "py", "token": {"access_token": "a"}}"#,
            )
            .unwrap();
            assert_eq!(store.client_secret(), "py");
            assert_eq!(store.get_token().unwrap()["access_token"], "a");
        }
        std::fs::write(store.path(), b"PLAIN1{broken").unwrap();
        assert!(store.set_token(Some(json!({"access_token": "t"}))).is_err());
        assert_eq!(std::fs::read(store.path()).unwrap(), b"PLAIN1{broken");
        store.set_client_secret("new").unwrap(); // entering the secret again repairs it
        assert_eq!(store.client_secret(), "new");
    }

    #[test]
    fn lock_is_exclusive() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path()).unwrap();
        let other = File::options()
            .create(true)
            .truncate(false)
            .write(true)
            .open(dir.path().join("secrets.lock"))
            .unwrap();
        other.lock().unwrap(); // held by "another process"
        let store = SecretsFile::new(dir.path().to_path_buf());
        let started = Instant::now();
        let busy = store.locked(|| Ok(()));
        assert!(busy.is_err() && started.elapsed() >= Duration::from_secs(4));
        other.unlock().unwrap();
        assert!(store.locked(|| Ok(())).is_ok());
    }
}
