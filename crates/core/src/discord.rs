//! Minimal client for Discord's local RPC (IPC) socket: named pipe on Windows,
//! Unix socket elsewhere. Commands and subscribed events never share a buffer.

use async_trait::async_trait;
use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::{oneshot, watch};

pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(10);
pub const AUTHORIZE_TIMEOUT: Duration = Duration::from_secs(300);

#[derive(Clone, Debug, PartialEq)]
pub enum RpcError {
    /// Discord is not running, or refused the connection.
    Unavailable(String),
    /// The connection was closed.
    Closed(String),
    /// Discord answered with an ERROR event.
    Rpc { code: Option<i64>, message: String },
    Timeout,
}

impl std::fmt::Display for RpcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RpcError::Unavailable(m) => write!(f, "Discord not available: {m}"),
            RpcError::Closed(m) => write!(f, "Discord connection closed: {m}"),
            RpcError::Rpc { code, message } => write!(f, "{message} (code {})", code.map_or("?".into(), |c| c.to_string())),
            RpcError::Timeout => write!(f, "Discord did not answer in time"),
        }
    }
}

/// Called for every subscribed event, e.g. ("VOICE_SETTINGS_UPDATE", {...}), from the
/// reader itself: an event is handled before any response that follows it.
pub type EventSink = Rc<dyn Fn(&str, Value)>;

/// One RPC connection, as the engine sees it (faked in tests).
#[async_trait(?Send)]
pub trait DiscordConnection {
    async fn command(&self, cmd: &str, args: Value, evt: Option<&str>, timeout: Duration) -> Result<Value, RpcError>;
    /// Resolves with the reason once the connection is closed.
    async fn wait_closed(&self) -> String;
    fn close(&self);
}

#[async_trait(?Send)]
pub trait DiscordConnector {
    async fn connect(&self, client_id: &str, events: EventSink) -> Result<Rc<dyn DiscordConnection>, RpcError>;
}

/// Typed helpers over the raw commands.
pub struct Rpc<'a>(pub &'a dyn DiscordConnection);

impl Rpc<'_> {
    async fn call(&self, cmd: &str, args: Value) -> Result<Value, RpcError> {
        self.0.command(cmd, args, None, DEFAULT_TIMEOUT).await
    }
    pub async fn authorize(&self, client_id: &str, scopes: &[&str]) -> Result<String, RpcError> {
        let data = self.0.command("AUTHORIZE", json!({"client_id": client_id, "scopes": scopes}), None,
                                  AUTHORIZE_TIMEOUT).await?;
        data["code"].as_str().map(str::to_string)
            .ok_or(RpcError::Rpc { code: None, message: "Discord returned no authorization code".into() })
    }
    pub async fn authenticate(&self, token: &str) -> Result<Value, RpcError> {
        self.call("AUTHENTICATE", json!({"access_token": token})).await
    }
    pub async fn get_voice_settings(&self) -> Result<Value, RpcError> {
        self.call("GET_VOICE_SETTINGS", json!({})).await
    }
    pub async fn set_voice_settings(&self, args: Value) -> Result<Value, RpcError> {
        self.call("SET_VOICE_SETTINGS", args).await
    }
    pub async fn subscribe(&self, evt: &str) -> Result<Value, RpcError> {
        self.0.command("SUBSCRIBE", json!({}), Some(evt), DEFAULT_TIMEOUT).await
    }
    pub async fn get_selected_voice_channel(&self) -> Result<Value, RpcError> {
        self.call("GET_SELECTED_VOICE_CHANNEL", json!({})).await
    }
    pub async fn get_guild(&self, guild_id: &str) -> Result<Value, RpcError> {
        self.call("GET_GUILD", json!({"guild_id": guild_id})).await
    }
    /// Join a voice channel, or leave the current one with None.
    pub async fn select_voice_channel(&self, channel_id: Option<&str>) -> Result<Value, RpcError> {
        self.call("SELECT_VOICE_CHANNEL", json!({"channel_id": channel_id, "force": true})).await
    }
}

type Reader = Box<dyn AsyncRead + Unpin>;
type Writer = Box<dyn AsyncWrite + Unpin>;
type Pending = Rc<RefCell<HashMap<String, oneshot::Sender<Result<Value, RpcError>>>>>;

#[cfg(windows)]
async fn open() -> Result<(Reader, Writer), RpcError> {
    use tokio::net::windows::named_pipe::ClientOptions;
    let mut last = String::from("no IPC pipe found");
    for i in 0..10 {
        match ClientOptions::new().open(format!(r"\\?\pipe\discord-ipc-{i}")) {
            Ok(pipe) => {
                let (r, w) = tokio::io::split(pipe);
                return Ok((Box::new(r), Box::new(w)));
            }
            Err(e) => last = e.to_string(),
        }
    }
    Err(RpcError::Unavailable(format!("Discord is not running ({last})")))
}

#[cfg(not(windows))]
pub fn ipc_paths() -> Vec<std::path::PathBuf> {
    let mut bases: Vec<String> = ["XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"]
        .iter().filter_map(|v| std::env::var(v).ok()).filter(|v| !v.is_empty()).collect();
    bases.push("/tmp".into());
    let mut out = Vec::new();
    for base in bases {
        for sub in ["", "app/com.discordapp.Discord", "snap.discord", ".flatpak/dev.vencord.Vesktop/xdg-run"] {
            for i in 0..10 {
                let path = std::path::Path::new(&base).join(sub).join(format!("discord-ipc-{i}"));
                if !out.contains(&path) {
                    out.push(path);
                }
            }
        }
    }
    out
}

#[cfg(not(windows))]
async fn open() -> Result<(Reader, Writer), RpcError> {
    let mut last = String::from("no IPC socket found");
    for path in ipc_paths() {
        if !path.exists() {
            continue;
        }
        match tokio::net::UnixStream::connect(&path).await {
            Ok(stream) => {
                let (r, w) = tokio::io::split(stream);
                return Ok((Box::new(r), Box::new(w)));
            }
            Err(e) => last = e.to_string(),
        }
    }
    Err(RpcError::Unavailable(format!("Discord is not running ({last})")))
}

fn frame(op: u32, payload: &Value) -> Vec<u8> {
    let body = payload.to_string().into_bytes();
    let mut out = Vec::with_capacity(8 + body.len());
    out.extend_from_slice(&op.to_le_bytes());
    out.extend_from_slice(&(body.len() as u32).to_le_bytes());
    out.extend_from_slice(&body);
    out
}

pub struct DiscordIpc {
    writer: Rc<tokio::sync::Mutex<Writer>>,
    pending: Pending,
    nonce: Cell<u64>,
    closed: watch::Receiver<Option<String>>,
    reader: RefCell<Option<tokio::task::JoinHandle<()>>>,
}

pub struct DiscordIpcConnector;

#[async_trait(?Send)]
impl DiscordConnector for DiscordIpcConnector {
    async fn connect(&self, client_id: &str, events: EventSink) -> Result<Rc<dyn DiscordConnection>, RpcError> {
        Ok(Rc::new(DiscordIpc::connect(client_id, events).await?))
    }
}

impl DiscordIpc {
    /// Open the socket, handshake and wait for READY (must run inside a LocalSet).
    pub async fn connect(client_id: &str, events: EventSink) -> Result<DiscordIpc, RpcError> {
        let (mut reader, mut writer) = open().await?;
        writer.write_all(&frame(0, &json!({"v": 1, "client_id": client_id}))).await
            .map_err(|e| RpcError::Unavailable(e.to_string()))?;
        let writer = Rc::new(tokio::sync::Mutex::new(writer));
        let pong_writer = writer.clone();
        let pending: Pending = Rc::default();
        let (ready_tx, ready_rx) = oneshot::channel::<Result<(), RpcError>>();
        let (closed_tx, closed_rx) = watch::channel(None);
        let reader_pending = pending.clone();
        let task = tokio::task::spawn_local(async move {
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
                if !payload.is_object() {
                    log::warn!("Ignoring unexpected Discord frame");
                    continue;
                }
                match op {
                    1 => {}
                    2 => break format!("{} (code {})", payload["message"].as_str().unwrap_or("closed by Discord"), payload["code"]),
                    3 => {
                        let _ = pong_writer.lock().await.write_all(&frame(4, &payload)).await; // PING -> PONG
                        continue;
                    }
                    _ => continue,
                }
                let evt = payload["evt"].as_str().unwrap_or_default().to_string();
                let data = payload["data"].clone();
                let error = || RpcError::Rpc {
                    code: data["code"].as_i64(),
                    message: data["message"].as_str().unwrap_or("unknown error").to_string(),
                };
                if let Some(nonce) = payload["nonce"].as_str() {
                    if let Some(tx) = reader_pending.borrow_mut().remove(nonce) {
                        let _ = tx.send(if evt == "ERROR" { Err(error()) } else { Ok(data) });
                        continue;
                    }
                }
                if evt == "READY" {
                    if let Some(tx) = ready.take() {
                        let _ = tx.send(Ok(()));
                    }
                } else if evt == "ERROR" && ready.is_some() {
                    let _ = ready.take().unwrap().send(Err(error()));
                } else if payload["cmd"] == "DISPATCH" && !evt.is_empty() {
                    events(&evt, data);
                }
            };
            for (_, tx) in reader_pending.borrow_mut().drain() {
                let _ = tx.send(Err(RpcError::Closed(reason.clone())));
            }
            if let Some(tx) = ready.take() {
                let _ = tx.send(Err(RpcError::Unavailable(reason.clone())));
            }
            let _ = closed_tx.send(Some(reason));
        });
        let ipc = DiscordIpc {
            writer,
            pending,
            nonce: Cell::new(1),
            closed: closed_rx,
            reader: RefCell::new(Some(task)),
        };
        match tokio::time::timeout(DEFAULT_TIMEOUT, ready_rx).await {
            Ok(Ok(Ok(()))) => Ok(ipc),
            Ok(Ok(Err(e))) => Err(e),
            _ => {
                ipc.close();
                Err(RpcError::Unavailable("Discord did not answer the RPC handshake".into()))
            }
        }
    }
}

#[async_trait(?Send)]
impl DiscordConnection for DiscordIpc {
    async fn command(&self, cmd: &str, args: Value, evt: Option<&str>, timeout: Duration) -> Result<Value, RpcError> {
        if let Some(reason) = self.closed.borrow().clone() {
            return Err(RpcError::Closed(reason));
        }
        let nonce = format!("n{}", self.nonce.get());
        self.nonce.set(self.nonce.get() + 1);
        let (tx, rx) = oneshot::channel();
        self.pending.borrow_mut().insert(nonce.clone(), tx);
        let mut payload = json!({"cmd": cmd, "args": args, "nonce": nonce});
        if let Some(evt) = evt {
            payload["evt"] = json!(evt);
        }
        let written = self.writer.lock().await.write_all(&frame(1, &payload)).await;
        let result = match written {
            Err(e) => Err(RpcError::Closed(e.to_string())),
            Ok(()) => match tokio::time::timeout(timeout, rx).await {
                Ok(Ok(result)) => result,
                Ok(Err(_)) => Err(RpcError::Closed("closed".into())),
                Err(_) => Err(RpcError::Timeout),
            },
        };
        self.pending.borrow_mut().remove(&nonce);
        result
    }

    async fn wait_closed(&self) -> String {
        let mut closed = self.closed.clone();
        loop {
            if let Some(reason) = closed.borrow().clone() {
                return reason;
            }
            if closed.changed().await.is_err() {
                return "closed".into();
            }
        }
    }

    fn close(&self) {
        if let Some(task) = self.reader.borrow_mut().take() {
            task.abort();
        }
        for (_, tx) in self.pending.borrow_mut().drain() {
            let _ = tx.send(Err(RpcError::Closed("closed".into())));
        }
    }
}

impl Drop for DiscordIpc {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use tokio::net::UnixListener;

    fn read_frame_sync(buf: &[u8]) -> Option<(u32, Value, usize)> {
        if buf.len() < 8 {
            return None;
        }
        let op = u32::from_le_bytes(buf[..4].try_into().unwrap());
        let len = u32::from_le_bytes(buf[4..8].try_into().unwrap()) as usize;
        if buf.len() < 8 + len {
            return None;
        }
        Some((op, serde_json::from_slice(&buf[8..8 + len]).unwrap_or(Value::Null), 8 + len))
    }

    /// Fake Discord client: READY, AUTHENTICATE, voice settings (event sent BEFORE the response).
    async fn fake_discord(listener: UnixListener, close_after: Option<usize>) {
        let (stream, _) = listener.accept().await.unwrap();
        let (mut r, mut w) = tokio::io::split(stream);
        let mut buf = Vec::new();
        let mut voice = json!({"mute": false, "deaf": false});
        let mut handled = 0;
        loop {
            let mut chunk = [0u8; 4096];
            let n = r.read(&mut chunk).await.unwrap_or(0);
            if n == 0 {
                return;
            }
            buf.extend_from_slice(&chunk[..n]);
            while let Some((op, msg, used)) = read_frame_sync(&buf) {
                buf.drain(..used);
                if op == 0 {
                    if msg["client_id"] == "bad" {
                        w.write_all(&frame(2, &json!({"code": 4000, "message": "Invalid Client ID"}))).await.unwrap();
                        return;
                    }
                    w.write_all(&frame(1, &json!({"cmd": "DISPATCH", "evt": "READY", "data": {"v": 1}}))).await.unwrap();
                    continue;
                }
                let mut reply = json!({"cmd": msg["cmd"], "nonce": msg["nonce"], "evt": null, "data": {}});
                match msg["cmd"].as_str().unwrap() {
                    "AUTHENTICATE" if msg["args"]["access_token"] != "good" => {
                        reply["evt"] = json!("ERROR");
                        reply["data"] = json!({"code": 4009, "message": "Invalid access token"});
                    }
                    "GET_VOICE_SETTINGS" => reply["data"] = voice.clone(),
                    "AUTHORIZE" => reply["data"] = json!({"code": "the-code"}),
                    "SET_VOICE_SETTINGS" => {
                        for (k, v) in msg["args"].as_object().unwrap() {
                            voice[k] = v.clone();
                        }
                        w.write_all(&frame(1, &json!({"cmd": "DISPATCH", "evt": "VOICE_SETTINGS_UPDATE",
                                                       "data": voice.clone(), "nonce": null}))).await.unwrap();
                        reply["data"] = voice.clone();
                    }
                    _ => {}
                }
                w.write_all(&frame(1, &reply)).await.unwrap();
                handled += 1;
                if Some(handled) == close_after {
                    return;
                }
            }
        }
    }

    /// The tests change XDG_RUNTIME_DIR: run them one at a time.
    static ENV: std::sync::Mutex<()> = std::sync::Mutex::new(());

    async fn with_fake<F: std::future::Future<Output = ()>>(close_after: Option<usize>, test: impl FnOnce() -> F) {
        let _guard = ENV.lock().unwrap_or_else(|e| e.into_inner());
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("XDG_RUNTIME_DIR", dir.path());
        let listener = UnixListener::bind(dir.path().join("discord-ipc-0")).unwrap();
        let local = tokio::task::LocalSet::new();
        local.spawn_local(fake_discord(listener, close_after));
        local.run_until(test()).await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn commands_and_events_are_not_mixed() {
        with_fake(None, || async {
            let seen: Rc<RefCell<Vec<(String, Value)>>> = Rc::default();
            let sink = seen.clone();
            let ipc = DiscordIpc::connect("123", Rc::new(move |e: &str, d: Value| sink.borrow_mut().push((e.into(), d))))
                .await.unwrap();
            let rpc = Rpc(&ipc);
            rpc.authenticate("good").await.unwrap();
            let result = rpc.set_voice_settings(json!({"mute": true})).await.unwrap();
            assert_eq!(result["mute"], true);
            assert_eq!(rpc.get_voice_settings().await.unwrap()["mute"], true);
            let (evt, data) = seen.borrow()[0].clone();
            assert_eq!((evt.as_str(), &data["mute"]), ("VOICE_SETTINGS_UPDATE", &json!(true)));
            assert_eq!(rpc.authorize("123", &["rpc"]).await.unwrap(), "the-code");
            let err = rpc.authenticate("expired").await.unwrap_err();
            assert_eq!(err, RpcError::Rpc { code: Some(4009), message: "Invalid access token".into() });
        }).await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn server_close_is_detected() {
        with_fake(Some(1), || async {
            let ipc = DiscordIpc::connect("123", Rc::new(|_: &str, _: Value| {})).await.unwrap();
            Rpc(&ipc).authenticate("good").await.unwrap();
            let reason = tokio::time::timeout(Duration::from_secs(2), ipc.wait_closed()).await.unwrap();
            assert!(reason.contains("connection lost"));
            assert!(matches!(Rpc(&ipc).get_voice_settings().await, Err(RpcError::Closed(_))));
        }).await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn invalid_client_id_is_refused() {
        with_fake(None, || async {
            match DiscordIpc::connect("bad", Rc::new(|_: &str, _: Value| {})).await {
                Err(RpcError::Unavailable(m)) => assert!(m.contains("Invalid Client ID"), "{m}"),
                other => panic!("unexpected {:?}", other.err()),
            }
        }).await;
    }
}
