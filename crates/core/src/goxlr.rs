//! GoXLR Utility websocket client and status model.

use async_trait::async_trait;
use futures_util::stream::SplitSink;
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeSet, HashMap};
use std::rc::Rc;
use std::time::Duration;
use tokio::net::TcpStream;
use tokio::sync::oneshot;
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

/// Large enough for a full DaemonStatus with several devices and file lists.
pub const MAX_MESSAGE_SIZE: usize = 16 * 1024 * 1024;

/// The parts of one GoXLR's state that matter for Discord.
#[derive(Clone, Debug, PartialEq, Default)]
pub struct MixerSnapshot {
    pub serial: String,
    pub cough_state: Option<String>,
    pub cough_mute_type: Option<String>,
    /// "A".."D", None when the channel is not on a fader.
    pub deafen_fader: Option<String>,
    pub deafen_state: Option<String>,
    pub deafen_mute_type: Option<String>,
    /// GoXLR buttons currently pressed.
    pub buttons_down: BTreeSet<String>,
}

/// Full DaemonStatus document, kept up to date with JSON patches.
#[derive(Clone, Debug, Default)]
pub struct StatusDoc {
    pub doc: Value,
}

fn text(v: &Value) -> Option<String> {
    v.as_str().map(str::to_string)
}

impl StatusDoc {
    pub fn new(doc: Value) -> Self {
        StatusDoc { doc }
    }

    /// Apply RFC 6902 operations; Err means the document must be reloaded.
    pub fn apply_patch(&mut self, ops: &Value) -> Result<(), String> {
        let patch: json_patch::Patch =
            serde_json::from_value(ops.clone()).map_err(|e| e.to_string())?;
        json_patch::patch(&mut self.doc, &patch).map_err(|e| e.to_string())
    }

    pub fn mixers(&self) -> Vec<String> {
        self.doc["mixers"]
            .as_object()
            .map(|m| m.keys().cloned().collect())
            .unwrap_or_default()
    }

    /// The configured device, or the first one when none is configured.
    ///
    /// A configured device that is not connected gives None: syncing another
    /// GoXLR instead would mute the wrong person's microphone.
    pub fn pick_serial(&self, preferred: &str) -> Option<String> {
        let mut mixers = self.mixers();
        mixers.sort();
        if !preferred.is_empty() {
            return mixers.into_iter().find(|s| s == preferred);
        }
        mixers.into_iter().next()
    }

    pub fn snapshot(&self, preferred_serial: &str, deafen_channel: &str) -> Option<MixerSnapshot> {
        let serial = self.pick_serial(preferred_serial)?;
        let mixer = &self.doc["mixers"][&serial];
        let mut fader = None;
        if let Some(faders) = mixer["fader_status"].as_object() {
            let mut names: Vec<&String> = faders.keys().collect();
            names.sort();
            fader = names
                .into_iter()
                .find(|n| faders[*n]["channel"] == deafen_channel)
                .map(|n| (n.clone(), &faders[n]));
        }
        let buttons_down = mixer["button_down"]
            .as_object()
            .map(|b| {
                b.iter()
                    .filter(|(_, down)| **down == Value::Bool(true))
                    .map(|(k, _)| k.clone())
                    .collect()
            })
            .unwrap_or_default();
        Some(MixerSnapshot {
            serial,
            cough_state: text(&mixer["cough_button"]["state"]),
            cough_mute_type: text(&mixer["cough_button"]["mute_type"]),
            deafen_state: fader.as_ref().and_then(|(_, f)| text(&f["mute_state"])),
            deafen_mute_type: fader.as_ref().and_then(|(_, f)| text(&f["mute_type"])),
            deafen_fader: fader.map(|(n, _)| n),
            buttons_down,
        })
    }
}

/// One session with GoXLR Utility, as the engine sees it (faked in tests).
#[async_trait(?Send)]
pub trait GoxlrConnection {
    /// Process messages until the connection ends. Every status change is
    /// announced on the `updates` channel given to the connector.
    async fn run(&self) -> Result<(), String>;
    /// Send a GoXLRCommand, e.g. {"SetCoughMuteState": "MutedToAll"}.
    async fn send_command(&self, serial: &str, command: Value) -> Result<(), String>;
    fn connected(&self) -> bool;
    fn status(&self) -> Option<StatusDoc>;
}

/// Called from the client every time the status changed (full status or patch).
pub type UpdateSink = Rc<dyn Fn()>;

pub trait GoxlrConnector {
    fn create(&self, url: &str, updates: UpdateSink) -> Rc<dyn GoxlrConnection>;
}

type Sink = SplitSink<WebSocketStream<MaybeTlsStream<TcpStream>>, Message>;

pub struct GoxlrClient {
    url: String,
    updates: UpdateSink,
    status: RefCell<Option<StatusDoc>>,
    sink: tokio::sync::Mutex<Option<Sink>>,
    pending: RefCell<HashMap<u64, oneshot::Sender<Value>>>,
    next_id: Cell<u64>,
}

pub struct GoxlrClientConnector;

impl GoxlrConnector for GoxlrClientConnector {
    fn create(&self, url: &str, updates: UpdateSink) -> Rc<dyn GoxlrConnection> {
        Rc::new(GoxlrClient {
            url: url.to_string(),
            updates,
            status: RefCell::new(None),
            sink: tokio::sync::Mutex::new(None),
            pending: RefCell::default(),
            next_id: Cell::new(1),
        })
    }
}

impl GoxlrClient {
    fn id(&self) -> u64 {
        let id = self.next_id.get();
        self.next_id.set(id + 1);
        id
    }

    async fn send(&self, payload: Value) -> Result<(), String> {
        let mut sink = self.sink.lock().await;
        let sink = sink.as_mut().ok_or("GoXLR Utility is not connected")?;
        sink.send(Message::text(payload.to_string()))
            .await
            .map_err(|e| e.to_string())
    }

    async fn request_status(&self) -> Result<(), String> {
        self.send(json!({"id": self.id(), "data": "GetStatus"}))
            .await
    }

    async fn handle(&self, raw: &str) -> Result<(), String> {
        let Ok(message) = serde_json::from_str::<Value>(raw) else {
            log::warn!("Ignoring non-JSON message from GoXLR Utility");
            return Ok(());
        };
        let data = &message["data"];
        if let Some(ops) = data.get("Patch") {
            let applied = match self.status.borrow_mut().as_mut() {
                None => return Ok(()), // the full status is on its way
                Some(status) => status.apply_patch(ops),
            };
            if let Err(e) = applied {
                log::warn!("Could not apply GoXLR patch ({e}), reloading full status");
                return self.request_status().await;
            }
            (self.updates)();
        } else if let Some(full) = data.get("Status") {
            *self.status.borrow_mut() = Some(StatusDoc::new(full.clone()));
            (self.updates)();
        } else if let Some(id) = message["id"].as_u64() {
            if let Some(tx) = self.pending.borrow_mut().remove(&id) {
                let _ = tx.send(data.clone());
            }
        }
        Ok(())
    }
}

#[async_trait(?Send)]
impl GoxlrConnection for GoxlrClient {
    async fn run(&self) -> Result<(), String> {
        let config = WebSocketConfig::default()
            .max_message_size(Some(MAX_MESSAGE_SIZE))
            .max_frame_size(Some(MAX_MESSAGE_SIZE));
        let connect = tokio_tungstenite::connect_async_with_config(&self.url, Some(config), false);
        let (ws, _) = tokio::time::timeout(Duration::from_secs(5), connect)
            .await
            .map_err(|_| "timeout".to_string())?
            .map_err(|e| e.to_string())?;
        let (sink, mut stream) = ws.split();
        *self.sink.lock().await = Some(sink);
        let result = async {
            self.request_status().await?;
            while let Some(message) = stream.next().await {
                match message.map_err(|e| e.to_string())? {
                    Message::Text(raw) => self.handle(&raw).await?,
                    Message::Close(_) => break,
                    _ => {}
                }
            }
            Ok::<(), String>(())
        }
        .await;
        *self.sink.lock().await = None;
        *self.status.borrow_mut() = None;
        self.pending.borrow_mut().clear(); // waiting commands fail with "closed"
        result.and(Err("connection closed by GoXLR Utility".into()))
    }

    async fn send_command(&self, serial: &str, command: Value) -> Result<(), String> {
        let id = self.id();
        let (tx, rx) = oneshot::channel();
        self.pending.borrow_mut().insert(id, tx);
        let sent = self
            .send(json!({"id": id, "data": {"Command": [serial, command.clone()]}}))
            .await;
        let result = match sent {
            Err(e) => Err(e),
            Ok(()) => match tokio::time::timeout(Duration::from_secs(5), rx).await {
                Err(_) => Err("timeout".into()),
                Ok(Err(_)) => Err("GoXLR Utility connection closed".into()),
                Ok(Ok(answer)) if answer.get("Error").is_some() => Err(format!(
                    "GoXLR Utility refused {command}: {}",
                    answer["Error"]
                )),
                Ok(Ok(_)) => Ok(()),
            },
        };
        self.pending.borrow_mut().remove(&id); // no leak when the answer never comes
        result
    }

    fn connected(&self) -> bool {
        self.status.borrow().is_some()
    }

    fn status(&self) -> Option<StatusDoc> {
        self.status.borrow().clone()
    }
}

/// A GoXLR seen by "Detect" in the settings: its serial and (fader, channel) pairs.
#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct Device {
    pub serial: String,
    pub faders: Vec<(String, String)>,
}

/// One-shot status read for the settings window (Send: usable from any thread).
pub async fn detect_devices(url: &str) -> Result<Vec<Device>, String> {
    let config = WebSocketConfig::default()
        .max_message_size(Some(MAX_MESSAGE_SIZE))
        .max_frame_size(Some(MAX_MESSAGE_SIZE));
    let connect = tokio_tungstenite::connect_async_with_config(url, Some(config), false);
    let (mut ws, _) = tokio::time::timeout(Duration::from_secs(3), connect)
        .await
        .map_err(|_| "timeout".to_string())?
        .map_err(|e| e.to_string())?;
    ws.send(Message::text(
        json!({"id": 1, "data": "GetStatus"}).to_string(),
    ))
    .await
    .map_err(|e| e.to_string())?;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(3);
    while let Ok(Some(message)) = tokio::time::timeout_at(deadline, ws.next()).await {
        let Ok(Message::Text(raw)) = message else {
            continue;
        };
        let Ok(value) = serde_json::from_str::<Value>(&raw) else {
            continue;
        };
        let Some(mixers) = value["data"]["Status"]["mixers"].as_object() else {
            continue;
        };
        let mut devices: Vec<Device> = mixers
            .iter()
            .map(|(serial, mixer)| {
                let mut faders: Vec<(String, String)> = mixer["fader_status"]
                    .as_object()
                    .map(|f| {
                        f.iter()
                            .map(|(name, info)| {
                                (
                                    name.clone(),
                                    info["channel"].as_str().unwrap_or("?").to_string(),
                                )
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                faders.sort();
                Device {
                    serial: serial.clone(),
                    faders,
                }
            })
            .collect();
        devices.sort_by(|a, b| a.serial.cmp(&b.serial));
        return Ok(devices);
    }
    Err("GoXLR Utility did not send its status".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    pub fn mixer(cough: &str, cough_type: &str) -> Value {
        json!({
            "hardware": {"device_type": "Full"},
            "cough_button": {"is_toggle": true, "mute_type": cough_type, "state": cough},
            "fader_status": {
                "A": {"channel": "Mic", "mute_type": "All", "mute_state": "Unmuted"},
                "B": {"channel": "Chat", "mute_type": "ToPhones", "mute_state": "Unmuted"},
                "C": {"channel": "Music", "mute_type": "All", "mute_state": "Unmuted"},
                "D": {"channel": "System", "mute_type": "All", "mute_state": "Unmuted"}},
            "levels": {"volumes": {"Chat": 200}}
        })
    }

    #[test]
    fn patch_errors_are_reported() {
        let mut st = StatusDoc::new(json!({"a": [1]}));
        assert!(st
            .apply_patch(&json!([{"op": "replace", "path": "/missing/key", "value": 1}]))
            .is_err());
        assert!(st
            .apply_patch(&json!([{"op": "replace", "path": "/a/5/b", "value": 1}]))
            .is_err());
        assert!(st.apply_patch(&json!("not a patch")).is_err());
    }

    #[test]
    fn snapshot_follows_fader_reassignment() {
        let mut st = StatusDoc::new(json!({"mixers": {"S1": mixer("Unmuted", "All")}}));
        let snap = st.snapshot("", "Chat").unwrap();
        assert_eq!(
            (
                snap.serial.as_str(),
                snap.deafen_fader.as_deref(),
                snap.deafen_mute_type.as_deref()
            ),
            ("S1", Some("B"), Some("ToPhones"))
        );
        st.apply_patch(&json!([
            {"op": "replace", "path": "/mixers/S1/fader_status/B/channel", "value": "Game"},
            {"op": "replace", "path": "/mixers/S1/fader_status/D/channel", "value": "Chat"}]))
            .unwrap();
        assert_eq!(
            st.snapshot("", "Chat").unwrap().deafen_fader.as_deref(),
            Some("D")
        );
        st.apply_patch(&json!([{"op": "replace", "path": "/mixers/S1/fader_status/D/channel", "value": "Music"}])).unwrap();
        let snap = st.snapshot("", "Chat").unwrap();
        assert!(snap.deafen_fader.is_none() && snap.deafen_state.is_none());
    }

    #[test]
    fn snapshot_picks_the_configured_device_only() {
        let st = StatusDoc::new(
            json!({"mixers": {"S2": mixer("MutedToAll", "All"), "S1": mixer("Unmuted", "All")}}),
        );
        assert_eq!(st.snapshot("", "Chat").unwrap().serial, "S1");
        assert_eq!(
            st.snapshot("S2", "Chat").unwrap().cough_state.as_deref(),
            Some("MutedToAll")
        );
        assert!(st.snapshot("unknown", "Chat").is_none());
        assert!(StatusDoc::new(json!({"mixers": {}}))
            .snapshot("", "Chat")
            .is_none());
    }

    #[test]
    fn snapshot_tolerates_unexpected_types_and_reads_buttons() {
        let st = StatusDoc::new(
            json!({"mixers": {"S1": {"cough_button": null, "fader_status": "?",
            "button_down": {"Bleep": true, "SamplerClear": false, "Odd": "yes"}}}}),
        );
        let snap = st.snapshot("", "Chat").unwrap();
        assert!(snap.cough_state.is_none() && snap.deafen_fader.is_none());
        assert_eq!(
            snap.buttons_down.into_iter().collect::<Vec<_>>(),
            vec!["Bleep".to_string()]
        );
    }

    #[tokio::test(flavor = "current_thread")]
    async fn client_status_patches_commands_and_reload() {
        use tokio::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let commands = std::sync::Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));
        let server_commands = commands.clone();
        let (patch_tx, mut patch_rx) = tokio::sync::mpsc::unbounded_channel::<Value>();
        tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let ws = tokio_tungstenite::accept_async(tcp).await.unwrap();
            let (mut tx, mut rx) = ws.split();
            loop {
                tokio::select! {
                    Some(Ok(Message::Text(raw))) = rx.next() => {
                        let msg: Value = serde_json::from_str(&raw).unwrap();
                        let reply = if msg["data"] == "GetStatus" {
                            json!({"id": msg["id"], "data": {"Status": {"mixers": {"S1": mixer("Unmuted", "All")}}}})
                        } else {
                            server_commands.lock().unwrap().push(msg["data"]["Command"].clone());
                            let bad = msg["data"]["Command"][1].get("Bad").is_some();
                            json!({"id": msg["id"], "data": if bad { json!({"Error": "nope"}) } else { json!("Ok") }})
                        };
                        tx.send(Message::text(reply.to_string())).await.unwrap();
                    }
                    Some(ops) = patch_rx.recv() => {
                        tx.send(Message::text(json!({"id": 0, "data": {"Patch": ops}}).to_string())).await.unwrap();
                    }
                    else => break,
                }
            }
        });
        let local = tokio::task::LocalSet::new();
        local.run_until(async move {
            let (up_tx, mut up_rx) = tokio::sync::mpsc::unbounded_channel();
            let client = GoxlrClientConnector.create(&format!("ws://127.0.0.1:{port}/api/websocket"),
                                                     Rc::new(move || { let _ = up_tx.send(()); }));
            let runner = client.clone();
            tokio::task::spawn_local(async move { runner.run().await });
            up_rx.recv().await.unwrap();
            let cough = |c: &Rc<dyn GoxlrConnection>| c.status().unwrap().snapshot("", "Chat").unwrap().cough_state;
            assert_eq!(cough(&client).as_deref(), Some("Unmuted"));
            patch_tx.send(json!([{"op": "replace", "path": "/mixers/S1/cough_button/state", "value": "MutedToX"}])).unwrap();
            up_rx.recv().await.unwrap();
            assert_eq!(cough(&client).as_deref(), Some("MutedToX"));

            client.send_command("S1", json!({"SetCoughMuteState": "Unmuted"})).await.unwrap();
            assert!(client.send_command("S1", json!({"Bad": 1})).await.is_err());
            assert_eq!(commands.lock().unwrap()[0], json!(["S1", {"SetCoughMuteState": "Unmuted"}]));

            // A patch that does not apply triggers a full status reload.
            patch_tx.send(json!([{"op": "replace", "path": "/mixers/S9/x", "value": 1}])).unwrap();
            up_rx.recv().await.unwrap();
            assert_eq!(cough(&client).as_deref(), Some("Unmuted"));
        }).await;
    }
}
