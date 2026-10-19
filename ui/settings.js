// Settings window: reads and writes through the Rust commands (src-tauri/src/settings.rs).
const { invoke } = window.__TAURI__.core;
const $ = (id) => document.getElementById(id);
let data = null;

const t = (text, values = {}) => {
  let out = (data && data.texts[text]) || text;
  for (const [k, v] of Object.entries(values)) out = out.replaceAll(`{${k}}`, v);
  return out;
};

function option(value, label, selected) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = label;
  o.selected = selected;
  return o;
}

function checkboxes(fieldsetId, selected) {
  const box = $(fieldsetId);
  for (const [key, label] of data.mute_targets) {
    const row = document.createElement("label");
    row.innerHTML = `<input type="checkbox" value="${key}"> <span></span>`;
    row.querySelector("span").textContent = t(label);
    row.querySelector("input").checked = selected.includes(key);
    box.append(row);
  }
}

function devices(list, current) {
  const select = $("mixer_serial");
  select.replaceChildren(option("", t("First GoXLR found"), !current));
  for (const serial of list) select.append(option(serial, serial, serial === current));
}

function selectTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", b.dataset.tab === name);
  for (const s of document.querySelectorAll("main section")) s.hidden = s.id !== `tab-${name}`;
}

async function load() {
  data = await invoke("get_settings");
  const cfg = data.config;
  document.documentElement.lang = data.language;
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  document.title = t("{app} – Settings", { app: "GoXLR Discord Sync" });
  $("autostart_label").textContent = t(data.platform === "windows" ? "Start with Windows" : "Start on login");

  $("first-run").hidden = Boolean(cfg.client_id && data.client_secret);
  $("redirect").value = data.redirect_uri;
  $("client_id").value = cfg.client_id;
  $("client_secret").value = data.client_secret;
  $("goxlr_url").value = cfg.goxlr_url;
  devices(cfg.mixer_serial ? [cfg.mixer_serial] : [], cfg.mixer_serial);
  for (const channel of data.fader_channels) $("deafen_channel").append(option(channel, channel, channel === cfg.deafen_channel));
  checkboxes("mute_triggers", cfg.mute_triggers);
  checkboxes("deafen_triggers", cfg.deafen_triggers);

  for (const [action, label] of data.button_actions) {
    const name = document.createElement("label");
    name.textContent = `${t(label)} :`.replace(" :", data.language === "fr" ? " :" : ":");
    name.htmlFor = `action_${action}`;
    const select = document.createElement("select");
    select.id = `action_${action}`;
    select.dataset.action = action;
    select.append(option("", t("Not assigned"), !cfg.button_actions[action]));
    for (const [key, text] of data.action_buttons) select.append(option(key, t(text), cfg.button_actions[action] === key));
    $("actions").append(name, select);
  }

  for (const key of ["cough_sync", "chat_sync", "bidirectional", "notifications", "check_updates"]) $(key).checked = cfg[key];
  $("autostart").checked = data.autostart;
  for (const [code, label] of data.languages) $("language").append(option(code, t(label), code === cfg.language));
}

function collect() {
  const cfg = { ...data.config };
  cfg.client_id = $("client_id").value.trim();
  cfg.goxlr_url = $("goxlr_url").value.trim();
  cfg.mixer_serial = $("mixer_serial").value;
  cfg.deafen_channel = $("deafen_channel").value;
  cfg.mute_triggers = [...$("mute_triggers").querySelectorAll("input:checked")].map((i) => i.value);
  cfg.deafen_triggers = [...$("deafen_triggers").querySelectorAll("input:checked")].map((i) => i.value);
  cfg.button_actions = {};
  for (const select of $("actions").querySelectorAll("select")) if (select.value) cfg.button_actions[select.dataset.action] = select.value;
  for (const key of ["cough_sync", "chat_sync", "bidirectional", "notifications", "check_updates"]) cfg[key] = $(key).checked;
  cfg.language = $("language").value;
  return cfg;
}

function problem(cfg, secret) {
  if (cfg.client_id || secret) {
    if (!cfg.client_id || !secret) return [t("Enter both the Client ID and the Client Secret."), "discord"];
    if (!/^\d+$/.test(cfg.client_id)) return [t("The Client ID is a number (the “Application ID” in the Developer Portal)."), "discord"];
  }
  if (!/^wss?:\/\//.test(cfg.goxlr_url)) return [t("The address must start with ws:// (default: {url}).", { url: data.default_goxlr_url }), "goxlr"];
  const buttons = Object.values(cfg.button_actions);
  if (new Set(buttons).size !== buttons.length) return [t("Each GoXLR button can only have one action."), "buttons"];
  return null;
}

async function save() {
  const cfg = collect();
  const secret = $("client_secret").value.trim();
  const found = problem(cfg, secret);
  if (found) {
    $("error").textContent = found[0];
    selectTab(found[1]);
    return;
  }
  $("save").disabled = true;
  try {
    await invoke("save_settings", { payload: { config: cfg, client_secret: secret, autostart: $("autostart").checked } });
    window.__TAURI__.window.getCurrentWindow().close();
  } catch (e) {
    $("error").textContent = String(e);
    $("save").disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
  $("portal").addEventListener("click", () => invoke("open_external", { url: "https://discord.com/developers/applications" }));
  $("copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("redirect").value);
    } catch {
      $("redirect").select();
      document.execCommand("copy");
    }
    $("copy").textContent = t("Copied");
    setTimeout(() => ($("copy").textContent = t("Copy")), 1500);
  });
  $("show_secret").addEventListener("change", (e) => ($("client_secret").type = e.target.checked ? "text" : "password"));
  $("check").addEventListener("click", async () => {
    const [id, secret] = [$("client_id").value.trim(), $("client_secret").value.trim()];
    const result = $("check_result");
    const found = problem({ client_id: id, goxlr_url: "ws://", button_actions: {} }, secret || " ");
    if (!id || !secret || found) {
      result.className = "note bad";
      result.textContent = found ? found[0] : t("Enter both the Client ID and the Client Secret.");
      return;
    }
    $("check").disabled = true;
    result.className = "note";
    result.textContent = t("Checking with Discord…");
    const [ok, message] = await invoke("check_credentials", { clientId: id, clientSecret: secret });
    result.className = ok ? "note good" : "note bad";
    result.textContent = message;
    $("check").disabled = false;
  });
  $("detect").addEventListener("click", async () => {
    const out = $("detect_result");
    $("detect").disabled = true;
    out.textContent = t("Contacting GoXLR Utility…");
    try {
      const found = await invoke("detect_devices", { url: $("goxlr_url").value.trim() });
      if (!found.length) {
        out.textContent = t("GoXLR Utility is running but no GoXLR is connected.");
      } else {
        devices(found.map((d) => d.serial), $("mixer_serial").value);
        out.textContent = found.map((d) => `${d.serial}: ` + d.faders.map(([f, c]) => t("fader {fader} = {channel}", { fader: f, channel: c })).join(", ")).join("\n");
      }
    } catch (e) {
      out.textContent = t("GoXLR Utility not reachable at this address ({error}).", { error: e });
    }
    $("detect").disabled = false;
  });
  $("open_log").addEventListener("click", () => invoke("open_log_folder"));
  $("cancel").addEventListener("click", () => window.__TAURI__.window.getCurrentWindow().close());
  $("save").addEventListener("click", save);
  await load();
  selectTab(location.hash.slice(1) || "discord");
});
