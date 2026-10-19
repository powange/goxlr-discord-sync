//! GoXLR Utility websocket client and status model, ported from goxlr.py.

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

#[derive(Clone, Debug, PartialEq)]
pub struct Snapshot {
    pub serial: String,
    pub cough_state: Option<String>,
    pub cough_mute_type: Option<String>,
    pub fader: Option<String>,
    pub fader_state: Option<String>,
    pub fader_mute_type: Option<String>,
}

fn text(v: &Value) -> Option<String> {
    v.as_str().map(str::to_string)
}

pub fn snapshot(status: &Value, deafen_channel: &str) -> Option<Snapshot> {
    let mixers = status.get("mixers")?.as_object()?;
    let serial = mixers.keys().min()?.clone();
    let mixer = &mixers[&serial];
    let faders = mixer["fader_status"].as_object();
    let (fader, info) = faders
        .and_then(|f| {
            let mut names: Vec<_> = f.keys().collect();
            names.sort();
            names.into_iter().find(|n| f[*n]["channel"] == deafen_channel).map(|n| (Some(n.clone()), f[n].clone()))
        })
        .unwrap_or((None, Value::Null));
    Some(Snapshot {
        serial,
        cough_state: text(&mixer["cough_button"]["state"]),
        cough_mute_type: text(&mixer["cough_button"]["mute_type"]),
        fader,
        fader_state: text(&info["mute_state"]),
        fader_mute_type: text(&info["mute_type"]),
    })
}

/// One websocket session: sends every status (full or patched) until the connection ends.
pub async fn run(url: &str, deafen_channel: &str, out: &mpsc::UnboundedSender<Option<Snapshot>>) -> Result<(), String> {
    let (mut ws, _) = tokio_tungstenite::connect_async(url).await.map_err(|e| e.to_string())?;
    ws.send(Message::text(json!({"id": 1, "data": "GetStatus"}).to_string())).await.map_err(|e| e.to_string())?;
    let mut status: Option<Value> = None;
    while let Some(message) = ws.next().await {
        let Ok(Message::Text(raw)) = message.map_err(|e| e.to_string()) else { continue };
        let Ok(msg) = serde_json::from_str::<Value>(&raw) else { continue };
        let data = &msg["data"];
        if let Some(full) = data.get("Status") {
            status = Some(full.clone());
        } else if let (Some(ops), Some(doc)) = (data.get("Patch"), status.as_mut()) {
            let ok = serde_json::from_value::<json_patch::Patch>(ops.clone())
                .ok()
                .is_some_and(|p| json_patch::patch(doc, &p).is_ok());
            if !ok {
                // Same recovery as the Python client: reload the full status.
                ws.send(Message::text(json!({"id": 2, "data": "GetStatus"}).to_string())).await
                    .map_err(|e| e.to_string())?;
                continue;
            }
        } else {
            continue;
        }
        let _ = out.send(status.as_ref().and_then(|s| snapshot(s, deafen_channel)));
    }
    Err("GoXLR Utility closed the connection".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn follows_fader_reassignment_through_patches() {
        let mut doc = json!({"mixers": {"S1": {
            "cough_button": {"state": "Unmuted", "mute_type": "All"},
            "fader_status": {"A": {"channel": "Music", "mute_state": "Unmuted", "mute_type": "All"},
                             "B": {"channel": "Chat", "mute_state": "Unmuted", "mute_type": "ToPhones"}}}}});
        assert_eq!(snapshot(&doc, "Chat").unwrap().fader.as_deref(), Some("B"));
        let ops: json_patch::Patch = serde_json::from_value(json!([
            {"op": "replace", "path": "/mixers/S1/fader_status/B/channel", "value": "Game"},
            {"op": "replace", "path": "/mixers/S1/fader_status/A/channel", "value": "Chat"},
            {"op": "replace", "path": "/mixers/S1/cough_button/state", "value": "MutedToX"}])).unwrap();
        json_patch::patch(&mut doc, &ops).unwrap();
        let snap = snapshot(&doc, "Chat").unwrap();
        assert_eq!(snap.fader.as_deref(), Some("A"));
        assert_eq!(snap.cough_state.as_deref(), Some("MutedToX"));
    }
}
