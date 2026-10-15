"""Settings window, started as a separate process with --settings.

The running application watches config.json and secrets.dat and applies the
changes on its own, so this window only has to write the files.
"""

import json
import logging
import tkinter as tk
from tkinter import messagebox, ttk

from . import APP_NAME, autostart, config, paths, secure_store, system
from .config import ACTION_BUTTONS, BUTTON_ACTIONS, FADER_CHANNELS, MUTE_TARGET_LABELS, MUTE_TARGETS
from .goxlr import MAX_MESSAGE_SIZE
from .i18n import LANGUAGES, tr
from .ui_common import DiscordCredentialsFrame, run_in_thread

log = logging.getLogger(__name__)

def first_device() -> str:
    return tr("First GoXLR found")


def no_button() -> str:
    return tr("Not assigned")


def detect_devices(url: str) -> dict[str, dict[str, str]]:
    """{serial: {fader: channel}} read from GoXLR Utility."""
    from websockets.sync.client import connect

    with connect(url, open_timeout=3, max_size=MAX_MESSAGE_SIZE) as ws:
        ws.send(json.dumps({"id": 1, "data": "GetStatus"}))
        for _ in range(20):
            message = json.loads(ws.recv(timeout=3))
            data = message.get("data")
            if isinstance(data, dict) and "Status" in data:
                mixers = data["Status"].get("mixers") or {}
                return {
                    serial: {name: info.get("channel", "?")
                             for name, info in sorted((m.get("fader_status") or {}).items())}
                    for serial, m in mixers.items()
                }
    raise RuntimeError("GoXLR Utility did not send its status")


class TriggerChecks(ttk.Frame):
    def __init__(self, parent, selected: list[str]):
        super().__init__(parent)
        self.vars = {}
        for target in MUTE_TARGETS:
            var = tk.BooleanVar(value=target in selected)
            ttk.Checkbutton(self, text=tr(MUTE_TARGET_LABELS[target]), variable=var).pack(anchor=tk.W)
            self.vars[target] = var

    @property
    def selected(self) -> list[str]:
        return [t for t, var in self.vars.items() if var.get()]


class SettingsWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = config.load()
        root.title(tr("{app} – Settings", app=APP_NAME))
        root.minsize(600, 520)

        outer = ttk.Frame(root, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True)

        notebook.add(self._discord_tab(notebook), text="Discord")
        notebook.add(self._goxlr_tab(notebook), text="GoXLR")
        notebook.add(self._buttons_tab(notebook), text=tr("Buttons"))
        notebook.add(self._behaviour_tab(notebook), text=tr("Behaviour"))

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text=tr("Save"), command=self.save).pack(side=tk.RIGHT)
        ttk.Button(buttons, text=tr("Cancel"), command=root.destroy).pack(side=tk.RIGHT, padx=8)
        ttk.Button(buttons, text=tr("Open log folder"),
                   command=lambda: system.open_path(paths.log_dir())).pack(side=tk.LEFT)

    def _discord_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=12)
        self.credentials = DiscordCredentialsFrame(
            tab, self.cfg.redirect_uri, self.cfg.client_id, secure_store.get_client_secret()
        )
        self.credentials.pack(fill=tk.X)
        return tab

    def _goxlr_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=12)
        tab.columnconfigure(1, weight=1)

        ttk.Label(tab, text=tr("GoXLR Utility address:")).grid(row=0, column=0, sticky=tk.W, pady=3)
        self.url_var = tk.StringVar(value=self.cfg.goxlr_url)
        ttk.Entry(tab, textvariable=self.url_var).grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=(8, 0))

        ttk.Label(tab, text=tr("Device:")).grid(row=1, column=0, sticky=tk.W, pady=3)
        self.serial_var = tk.StringVar(value=self.cfg.mixer_serial or first_device())
        known = [self.cfg.mixer_serial] if self.cfg.mixer_serial else []
        self.serial_box = ttk.Combobox(tab, textvariable=self.serial_var, values=[first_device(), *known])
        self.serial_box.grid(row=1, column=1, sticky=tk.EW, padx=8)
        self.detect_btn = ttk.Button(tab, text=tr("Detect"), command=self.detect)
        self.detect_btn.grid(row=1, column=2)
        self.detect_label = ttk.Label(tab, text="", foreground="gray", wraplength=520, justify=tk.LEFT)
        self.detect_label.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(2, 8))

        ttk.Label(tab, text=tr("Channel that deafens Discord:")).grid(row=3, column=0, sticky=tk.W, pady=3)
        self.channel_var = tk.StringVar(value=self.cfg.deafen_channel)
        ttk.Combobox(tab, textvariable=self.channel_var, values=FADER_CHANNELS, state="readonly"
                     ).grid(row=3, column=1, sticky=tk.W, padx=8)

        triggers = ttk.Frame(tab)
        triggers.grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=(12, 0))
        triggers.columnconfigure((0, 1), weight=1)
        left = ttk.LabelFrame(triggers, text=tr("Cough button mutes Discord when"), padding=8)
        left.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 6))
        self.mute_triggers = TriggerChecks(left, self.cfg.mute_triggers)
        self.mute_triggers.pack(anchor=tk.W)
        right = ttk.LabelFrame(triggers, text=tr("Channel fader deafens Discord when"), padding=8)
        right.grid(row=0, column=1, sticky=tk.NSEW, padx=(6, 0))
        self.deafen_triggers = TriggerChecks(right, self.cfg.deafen_triggers)
        self.deafen_triggers.pack(anchor=tk.W)
        ttk.Label(tab, foreground="gray", wraplength=540, justify=tk.LEFT, text=tr(
            "“Muted to everything” is the hold-to-mute state. The other entries match the mute "
            "function configured for the button or fader in GoXLR Utility. Example: leave “Muted to "
            "Stream” unchecked to hide a sound from your stream without muting yourself in Discord."
        )).grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))
        return tab

    def _buttons_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=12)
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, wraplength=540, justify=tk.LEFT, text=tr(
            "Give a Discord action to a GoXLR button. The button keeps its usual GoXLR function: "
            "an empty sampler slot or an unused effect button works best."
        )).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 12))
        self.button_labels = {key: tr(label) for key, label in ACTION_BUTTONS.items()}
        labels = [no_button(), *self.button_labels.values()]
        self.action_vars = {}
        for row, (action, label) in enumerate(BUTTON_ACTIONS.items(), start=1):
            ttk.Label(tab, text=tr(label) + ":").grid(row=row, column=0, sticky=tk.W, pady=4)
            current = self.button_labels.get(self.cfg.button_actions.get(action, ""), no_button())
            var = tk.StringVar(value=current)
            ttk.Combobox(tab, textvariable=var, values=labels, state="readonly", width=28
                         ).grid(row=row, column=1, sticky=tk.W, padx=8, pady=4)
            self.action_vars[action] = var
        return tab

    def _button_actions(self) -> dict[str, str] | None:
        by_label = {label: key for key, label in self.button_labels.items()}
        chosen = {a: by_label[v.get()] for a, v in self.action_vars.items() if v.get() in by_label}
        if len(set(chosen.values())) != len(chosen):
            messagebox.showerror(tr("Buttons"), tr("Each GoXLR button can only have one action."), parent=self.root)
            return None
        return chosen

    def _behaviour_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=12)
        self.bool_vars = {}
        options = [
            ("cough_sync", tr("Cough button mutes Discord")),
            ("chat_sync", tr("Channel fader mute deafens Discord")),
            ("bidirectional", tr("Two-way sync: muting or deafening in Discord updates the GoXLR")),
            ("notifications", tr("Show notifications when a connection is lost or restored")),
            ("check_updates", tr("Check for new versions once a day")),
        ]
        for name, label in options:
            var = tk.BooleanVar(value=getattr(self.cfg, name))
            ttk.Checkbutton(tab, text=label, variable=var).pack(anchor=tk.W, pady=2)
            self.bool_vars[name] = var
        try:
            enabled = autostart.is_enabled()
        except OSError:
            enabled = False
        self.autostart_var = tk.BooleanVar(value=enabled)
        label = tr("Start with Windows") if paths.IS_WINDOWS else tr("Start on login")
        ttk.Checkbutton(tab, text=label, variable=self.autostart_var).pack(anchor=tk.W, pady=2)

        row = ttk.Frame(tab)
        row.pack(anchor=tk.W, pady=(14, 0))
        ttk.Label(row, text=tr("Language:")).pack(side=tk.LEFT)
        self.language_labels = {code: tr(name) for code, name in LANGUAGES.items()}
        current = self.language_labels.get(self.cfg.language, self.language_labels["auto"])
        self.language_var = tk.StringVar(value=current)
        ttk.Combobox(row, textvariable=self.language_var, values=list(self.language_labels.values()),
                     state="readonly", width=16).pack(side=tk.LEFT, padx=8)
        return tab

    def detect(self) -> None:
        url = self.url_var.get().strip()
        self.detect_btn.configure(state=tk.DISABLED)
        self.detect_label.configure(text=tr("Contacting GoXLR Utility…"))

        def _done(result):
            self.detect_btn.configure(state=tk.NORMAL)
            if isinstance(result, Exception):
                self.detect_label.configure(
                    text=tr("GoXLR Utility not reachable at this address ({error}).", error=result))
                return
            if not result:
                self.detect_label.configure(text=tr("GoXLR Utility is running but no GoXLR is connected."))
                return
            self.serial_box.configure(values=[first_device(), *result])
            lines = [f"{serial}: " + ", ".join(tr("fader {fader} = {channel}", fader=f, channel=c)
                                               for f, c in faders.items())
                     for serial, faders in result.items()]
            self.detect_label.configure(text="\n".join(lines))

        run_in_thread(self.root, lambda: detect_devices(url), _done)

    def collect(self) -> config.Config | None:
        """The configuration shown in the window, or None after showing what is wrong."""
        creds = self.credentials
        if creds.client_id or creds.client_secret:
            problem = creds.validate_format()
            if problem:
                messagebox.showerror(tr("Discord application"), problem, parent=self.root)
                return None
        url = self.url_var.get().strip()
        if not url.startswith(("ws://", "wss://")):
            messagebox.showerror("GoXLR Utility", tr("The address must start with ws:// (default: {url}).",
                                                     url=config.DEFAULT_GOXLR_URL), parent=self.root)
            return None
        button_actions = self._button_actions()
        if button_actions is None:
            return None
        serial = self.serial_var.get().strip()
        language = next((code for code, label in self.language_labels.items()
                         if label == self.language_var.get()), "auto")
        return self.cfg.updated(
            client_id=creds.client_id,
            goxlr_url=url,
            mixer_serial="" if serial == first_device() else serial,
            deafen_channel=self.channel_var.get(),
            mute_triggers=self.mute_triggers.selected,
            deafen_triggers=self.deafen_triggers.selected,
            button_actions=button_actions,
            language=language,
            **{name: var.get() for name, var in self.bool_vars.items()},
        )

    def save(self) -> None:
        cfg = self.collect()
        if cfg is None:
            return
        creds = self.credentials
        try:
            if cfg.client_id != self.cfg.client_id:
                secure_store.set_token(None)
            if creds.client_secret != secure_store.get_client_secret():
                secure_store.set_client_secret(creds.client_secret)
            config.save(cfg)
            if self.autostart_var.get() != autostart.is_enabled():
                (autostart.enable if self.autostart_var.get() else autostart.disable)()
        except OSError as e:
            messagebox.showerror(tr("Settings"), tr("Could not save the settings:") + f"\n{e}", parent=self.root)
            return
        log.info("Settings saved")
        self.root.destroy()


def run_settings() -> None:
    instance = system.SingleInstance("Settings")
    if not instance.acquire():
        log.info("The settings window is already open")
        return
    root = tk.Tk()
    SettingsWindow(root)
    root.mainloop()
