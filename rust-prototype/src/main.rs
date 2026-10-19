//! Rust prototype of GoXLR Discord Sync, to measure memory, size and start-up time.
//!
//! Default "observe" mode: connects to GoXLR Utility and Discord with the Python
//! app's configuration and token, follows the Cough button and Chat fader, and
//! only logs the change it would send. `--live` sends it (quit the Python app first).
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod discord;
mod goxlr;
mod store;
mod sync;

use serde_json::{json, Value};
use std::io::Write;
use std::time::{Duration, Instant};
use tao::event::{Event, StartCause};
use tao::event_loop::{ControlFlow, EventLoopBuilder, EventLoopProxy};
use tokio::sync::mpsc;
use tray_icon::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{Icon, TrayIconBuilder};

enum UiEvent {
    Status { color: [u8; 3], text: String },
    Menu(MenuEvent),
}

static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();

fn log(message: &str) {
    let t = START.get_or_init(Instant::now).elapsed().as_secs_f64();
    let line = format!("[{t:9.3}s] {message}\n");
    eprint!("{line}");
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true)
        .open(std::env::temp_dir().join("goxlr-rust-proto.log")) {
        let _ = f.write_all(line.as_bytes());
    }
}

fn circle_icon(rgb: [u8; 3]) -> Icon {
    const N: usize = 32;
    let mut rgba = vec![0u8; N * N * 4];
    for y in 0..N {
        for x in 0..N {
            let (dx, dy) = (x as f32 - 15.5, y as f32 - 15.5);
            let alpha = (15.5 - (dx * dx + dy * dy).sqrt()).clamp(0.0, 1.0);
            let i = (y * N + x) * 4;
            rgba[i..i + 4].copy_from_slice(&[rgb[0], rgb[1], rgb[2], (alpha * 255.0) as u8]);
        }
    }
    Icon::from_rgba(rgba, N as u32, N as u32).expect("valid icon")
}

const GREEN: [u8; 3] = [46, 160, 67];
const RED: [u8; 3] = [218, 54, 51];
const ORANGE: [u8; 3] = [230, 140, 20];
const GREY: [u8; 3] = [120, 124, 135];

#[derive(Default)]
struct State {
    snapshot: Option<goxlr::Snapshot>,
    goxlr_up: bool,
    discord_up: bool,
    mute: Option<bool>,
    deaf: Option<bool>,
}

fn status(state: &State, live: bool) -> (Vec<u8>, String) {
    let mode = if live { "" } else { " [observe]" };
    let (color, text) = if !state.discord_up {
        (ORANGE, "Waiting for Discord".to_string())
    } else if !state.goxlr_up {
        (ORANGE, "Waiting for GoXLR Utility".to_string())
    } else {
        let mic = if state.mute == Some(true) { "Mic muted" } else { "Mic live" };
        let audio = if state.deaf == Some(true) { "deafened" } else { "hearing" };
        let color = if state.mute == Some(true) || state.deaf == Some(true) { RED } else { GREEN };
        (color, format!("{mic}, {audio}"))
    };
    (color.to_vec(), format!("{text}{mode}"))
}

async fn reconcile(state: &mut State, settings: &store::Settings, discord: Option<&discord::Discord>, live: bool) {
    let (Some(snap), Some(discord)) = (state.snapshot.clone(), discord) else { return };
    let want_mute = sync::desired_flag(snap.cough_state.as_deref(), snap.cough_mute_type.as_deref(), &settings.mute_triggers);
    let want_deaf = snap.fader.as_ref().and_then(|_| {
        sync::desired_flag(snap.fader_state.as_deref(), snap.fader_mute_type.as_deref(), &settings.deafen_triggers)
    });
    for _ in 0..3 {
        let Some((field, value)) = sync::plan(want_mute, want_deaf, state.mute, state.deaf) else { return };
        if !live {
            log(&format!("observe: would send SET_VOICE_SETTINGS {field}={value}"));
            return;
        }
        let started = Instant::now();
        match discord.command("SET_VOICE_SETTINGS", json!({ field: value }), None).await {
            Ok(result) => {
                state.mute = result["mute"].as_bool().or(state.mute);
                state.deaf = result["deaf"].as_bool().or(state.deaf);
                log(&format!("Discord {field}={value} ({} ms)", started.elapsed().as_millis()));
            }
            Err(e) => {
                log(&format!("Discord refused {field}={value}: {e}"));
                return;
            }
        }
    }
}

async fn engine(ui: EventLoopProxy<UiEvent>, live: bool) {
    let publish = |state: &State| {
        let (color, text) = status(state, live);
        let _ = ui.send_event(UiEvent::Status { color: [color[0], color[1], color[2]], text });
    };
    let settings = match store::load() {
        Ok(s) => s,
        Err(e) => {
            log(&format!("cannot start: {e}"));
            let _ = ui.send_event(UiEvent::Status { color: GREY, text: e });
            return;
        }
    };
    log(&format!("settings loaded (client {}, GoXLR {}), mode {}", settings.client_id, settings.goxlr_url,
                 if live { "live" } else { "observe" }));

    let (gx_tx, mut gx_rx) = mpsc::unbounded_channel();
    let (url, channel) = (settings.goxlr_url.clone(), settings.deafen_channel.clone());
    tokio::spawn(async move {
        loop {
            if let Err(e) = goxlr::run(&url, &channel, &gx_tx).await {
                log(&format!("GoXLR Utility: {e}"));
            }
            let _ = gx_tx.send(None);
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    });

    let mut state = State::default();
    let (ev_tx, mut ev_rx) = mpsc::unbounded_channel::<Value>();
    let mut discord: Option<discord::Discord> = None;
    let mut closed: Option<tokio::sync::oneshot::Receiver<String>> = None;
    let mut next_connect = tokio::time::Instant::now();
    publish(&state);
    loop {
        tokio::select! {
            Some(snap) = gx_rx.recv() => {
                if state.goxlr_up != snap.is_some() {
                    log(if snap.is_some() { "connected to GoXLR Utility" } else { "GoXLR Utility disconnected" });
                }
                if let (Some(old), Some(new)) = (&state.snapshot, &snap) {
                    if old.cough_state != new.cough_state {
                        log(&format!("Cough: {:?} -> {:?}", old.cough_state, new.cough_state));
                    }
                    if old.fader_state != new.fader_state {
                        log(&format!("{} fader: {:?} -> {:?}", settings.deafen_channel, old.fader_state, new.fader_state));
                    }
                }
                state.goxlr_up = snap.is_some();
                state.snapshot = snap;
                reconcile(&mut state, &settings, discord.as_ref(), live).await;
            }
            Some(voice) = ev_rx.recv() => {
                state.mute = voice["mute"].as_bool().or(state.mute);
                state.deaf = voice["deaf"].as_bool().or(state.deaf);
            }
            reason = async { closed.as_mut().unwrap().await }, if closed.is_some() => {
                log(&format!("Discord disconnected: {}", reason.unwrap_or_default()));
                (discord, closed, state.discord_up) = (None, None, false);
                next_connect = tokio::time::Instant::now() + Duration::from_secs(2);
            }
            _ = tokio::time::sleep_until(next_connect), if discord.is_none() => {
                match connect_discord(&settings, ev_tx.clone()).await {
                    Ok((client, rx, voice)) => {
                        state.mute = voice["mute"].as_bool();
                        state.deaf = voice["deaf"].as_bool();
                        state.discord_up = true;
                        (discord, closed) = (Some(client), Some(rx));
                        log("connected to Discord");
                        reconcile(&mut state, &settings, discord.as_ref(), live).await;
                    }
                    Err(e) => {
                        log(&format!("Discord: {e}"));
                        next_connect = tokio::time::Instant::now() + Duration::from_secs(5);
                    }
                }
            }
        }
        publish(&state);
    }
}

async fn connect_discord(settings: &store::Settings, events: mpsc::UnboundedSender<Value>)
    -> Result<(discord::Discord, tokio::sync::oneshot::Receiver<String>, Value), String> {
    let (client, closed) = discord::Discord::connect(&settings.client_id, events).await?;
    client.command("AUTHENTICATE", json!({"access_token": settings.access_token}), None).await?;
    let voice = client.command("GET_VOICE_SETTINGS", json!({}), None).await?;
    client.command("SUBSCRIBE", json!({}), Some("VOICE_SETTINGS_UPDATE")).await?;
    Ok((client, closed, voice))
}

fn main() {
    START.get_or_init(Instant::now);
    let live = std::env::args().any(|a| a == "--live");
    log(&format!("GoXLR Discord Sync Rust prototype {} starting", env!("CARGO_PKG_VERSION")));

    let event_loop = EventLoopBuilder::<UiEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let menu_proxy = proxy.clone();
    MenuEvent::set_event_handler(Some(move |e| {
        let _ = menu_proxy.send_event(UiEvent::Menu(e));
    }));
    std::thread::spawn(move || {
        tokio::runtime::Builder::new_current_thread().enable_all().build()
            .expect("tokio runtime")
            .block_on(engine(proxy, live));
    });

    let status_item = MenuItem::new("Starting...", false, None);
    let quit_item = MenuItem::new("Quit", true, None);
    let mode_item = MenuItem::new(if live { "Mode: live" } else { "Mode: observe (sends nothing)" }, false, None);
    let menu = Menu::new();
    menu.append_items(&[&status_item, &mode_item, &PredefinedMenuItem::separator(), &quit_item]).expect("menu");
    let mut tray = None;
    let mut menu = Some(menu);

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::NewEvents(StartCause::Init) => {
                tray = Some(TrayIconBuilder::new()
                    .with_menu(Box::new(menu.take().unwrap()))
                    .with_tooltip("GoXLR Discord Sync (Rust prototype)")
                    .with_icon(circle_icon(GREY))
                    .build()
                    .expect("tray icon"));
                log(&format!("tray ready after {} ms", START.get().unwrap().elapsed().as_millis()));
            }
            Event::UserEvent(UiEvent::Status { color, text }) => {
                if let Some(tray) = &tray {
                    let _ = tray.set_icon(Some(circle_icon(color)));
                    let _ = tray.set_tooltip(Some(format!("GoXLR Discord Sync (Rust) - {text}")));
                }
                status_item.set_text(&text);
            }
            Event::UserEvent(UiEvent::Menu(e)) if e.id == *quit_item.id() => {
                log("quit");
                tray.take();
                *control_flow = ControlFlow::Exit;
            }
            _ => {}
        }
    });
}
