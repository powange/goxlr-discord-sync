//! Discord local RPC client (named pipe on Windows, Unix socket elsewhere), ported from discord_ipc.py.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, ReadHalf, WriteHalf};
use tokio::sync::{mpsc, oneshot};

type Pending = Arc<Mutex<HashMap<String, oneshot::Sender<Result<Value, String>>>>>;
type Writer = Box<dyn AsyncWrite + Unpin + Send>;

pub struct Discord {
    writer: tokio::sync::Mutex<Writer>,
    pending: Pending,
    nonce: AtomicU64,
}

#[cfg(windows)]
async fn open() -> Result<(Box<dyn AsyncRead + Unpin + Send>, Writer), String> {
    use tokio::net::windows::named_pipe::ClientOptions;
    for i in 0..10 {
        if let Ok(pipe) = ClientOptions::new().open(format!(r"\\?\pipe\discord-ipc-{i}")) {
            let (r, w): (ReadHalf<_>, WriteHalf<_>) = tokio::io::split(pipe);
            return Ok((Box::new(r), Box::new(w)));
        }
    }
    Err("Discord is not running".into())
}

#[cfg(not(windows))]
async fn open() -> Result<(Box<dyn AsyncRead + Unpin + Send>, Writer), String> {
    let mut bases: Vec<String> = ["XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"]
        .iter().filter_map(|v| std::env::var(v).ok()).collect();
    bases.push("/tmp".into());
    for base in bases {
        for sub in ["", "app/com.discordapp.Discord", "snap.discord"] {
            for i in 0..10 {
                let path = std::path::Path::new(&base).join(sub).join(format!("discord-ipc-{i}"));
                if let Ok(stream) = tokio::net::UnixStream::connect(&path).await {
                    let (r, w): (ReadHalf<_>, WriteHalf<_>) = tokio::io::split(stream);
                    return Ok((Box::new(r), Box::new(w)));
                }
            }
        }
    }
    Err("Discord is not running".into())
}

fn frame(op: u32, payload: &Value) -> Vec<u8> {
    let body = payload.to_string().into_bytes();
    let mut out = Vec::with_capacity(8 + body.len());
    out.extend_from_slice(&op.to_le_bytes());
    out.extend_from_slice(&(body.len() as u32).to_le_bytes());
    out.extend_from_slice(&body);
    out
}

impl Discord {
    /// Connect, handshake, wait for READY. Voice settings events go to `events`;
    /// the returned receiver yields the reason when the connection closes.
    pub async fn connect(client_id: &str, events: mpsc::UnboundedSender<Value>)
        -> Result<(Discord, oneshot::Receiver<String>), String> {
        let (mut reader, mut writer) = open().await?;
        writer.write_all(&frame(0, &json!({"v": 1, "client_id": client_id}))).await.map_err(|e| e.to_string())?;
        let pending: Pending = Arc::default();
        let (ready_tx, ready_rx) = oneshot::channel::<Result<(), String>>();
        let (closed_tx, closed_rx) = oneshot::channel();
        let reader_pending = pending.clone();
        tokio::spawn(async move {
            let mut ready = Some(ready_tx);
            let reason = loop {
                let mut header = [0u8; 8];
                if let Err(e) = reader.read_exact(&mut header).await {
                    break format!("connection lost ({e})");
                }
                let op = u32::from_le_bytes(header[..4].try_into().unwrap());
                let len = u32::from_le_bytes(header[4..].try_into().unwrap()) as usize;
                let mut body = vec![0u8; len];
                if let Err(e) = reader.read_exact(&mut body).await {
                    break format!("connection lost ({e})");
                }
                let payload: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
                if op == 2 {
                    break format!("closed by Discord: {}", payload["message"]);
                }
                if op != 1 {
                    continue;
                }
                let evt = payload["evt"].as_str().unwrap_or_default();
                if let Some(nonce) = payload["nonce"].as_str() {
                    if let Some(tx) = reader_pending.lock().unwrap().remove(nonce) {
                        let result = if evt == "ERROR" {
                            Err(payload["data"]["message"].as_str().unwrap_or("error").to_string())
                        } else {
                            Ok(payload["data"].clone())
                        };
                        let _ = tx.send(result);
                        continue;
                    }
                }
                if evt == "READY" {
                    if let Some(tx) = ready.take() {
                        let _ = tx.send(Ok(()));
                    }
                } else if payload["cmd"] == "DISPATCH" && evt == "VOICE_SETTINGS_UPDATE" {
                    let _ = events.send(payload["data"].clone());
                }
            };
            reader_pending.lock().unwrap().clear(); // pending commands fail with "closed"
            if let Some(tx) = ready.take() {
                let _ = tx.send(Err(reason.clone()));
            }
            let _ = closed_tx.send(reason);
        });
        match tokio::time::timeout(std::time::Duration::from_secs(10), ready_rx).await {
            Ok(Ok(Ok(()))) => {}
            Ok(Ok(Err(e))) => return Err(e),
            _ => return Err("Discord did not answer the handshake".into()),
        }
        let discord = Discord { writer: tokio::sync::Mutex::new(writer), pending, nonce: AtomicU64::new(1) };
        Ok((discord, closed_rx))
    }

    pub async fn command(&self, cmd: &str, args: Value, evt: Option<&str>) -> Result<Value, String> {
        let nonce = format!("n{}", self.nonce.fetch_add(1, Ordering::Relaxed));
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().insert(nonce.clone(), tx);
        let mut payload = json!({"cmd": cmd, "args": args, "nonce": nonce});
        if let Some(evt) = evt {
            payload["evt"] = json!(evt);
        }
        self.writer.lock().await.write_all(&frame(1, &payload)).await.map_err(|e| e.to_string())?;
        match tokio::time::timeout(std::time::Duration::from_secs(10), rx).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("connection closed".into()),
            Err(_) => {
                self.pending.lock().unwrap().remove(&nonce);
                Err("timeout".into())
            }
        }
    }
}
