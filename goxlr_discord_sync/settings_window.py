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
from .ui_common import DiscordCredentialsFrame, run_in_thread

log = logging.getLogger(__name__)

FIRST_DEVICE = "First GoXLR found"
NO_BUTTON = "Not assigned"


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
            ttk.Checkbutton(self, text=MUTE_TARGET_LABELS[target], variable=var).pack(anchor=tk.W)
            self.vars[target] = var

    @property
    def selected(self) -> list[str]:
        return [t for t, var in self.vars.items() if var.get()]


class SettingsWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = config.load()
        root.title(f"{APP_NAME} – Settings")
        root.minsize(600, 520)

        outer = ttk.Frame(root, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True)

        notebook.add(self._discord_tab(notebook), text="Discord")
        notebook.add(self._goxlr_tab(notebook), text="GoXLR")
        notebook.add(self._buttons_tab(notebook), text="Buttons")
        notebook.add(self._behaviour_tab(notebook), text="Behaviour")

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Save", command=self.save).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side=tk.RIGHT, padx=8)
        ttk.Button(buttons, text="Open log folder",
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

        ttk.Label(tab, text="GoXLR Utility address:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.url_var = tk.StringVar(value=self.cfg.goxlr_url)
        ttk.Entry(tab, textvariable=self.url_var).grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=(8, 0))

        ttk.Label(tab, text="Device:").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.serial_var = tk.StringVar(value=self.cfg.mixer_serial or FIRST_DEVICE)
        known = [self.cfg.mixer_serial] if self.cfg.mixer_serial else []
        self.serial_box = ttk.Combobox(tab, textvariable=self.serial_var, values=[FIRST_DEVICE, *known])
        self.serial_box.grid(row=1, column=1, sticky=tk.EW, padx=8)
        self.detect_btn = ttk.Button(tab, text="Detect", command=self.detect)
        self.detect_btn.grid(row=1, column=2)
        self.detect_label = ttk.Label(tab, text="", foreground="gray", wraplength=520, justify=tk.LEFT)
        self.detect_label.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(2, 8))

        ttk.Label(tab, text="Channel that deafens Discord:").grid(row=3, column=0, sticky=tk.W, pady=3)
        self.channel_var = tk.StringVar(value=self.cfg.deafen_channel)
        ttk.Combobox(tab, textvariable=self.channel_var, values=FADER_CHANNELS, state="readonly"
                     ).grid(row=3, column=1, sticky=tk.W, padx=8)

        triggers = ttk.Frame(tab)
        triggers.grid(row=4, column=0, columnspan=3, sticky=tk.EW, pady=(12, 0))
        triggers.columnconfigure((0, 1), weight=1)
        left = ttk.LabelFrame(triggers, text="Cough button mutes Discord when", padding=8)
        left.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 6))
        self.mute_triggers = TriggerChecks(left, self.cfg.mute_triggers)
        self.mute_triggers.pack(anchor=tk.W)
        right = ttk.LabelFrame(triggers, text="Channel fader deafens Discord when", padding=8)
        right.grid(row=0, column=1, sticky=tk.NSEW, padx=(6, 0))
        self.deafen_triggers = TriggerChecks(right, self.cfg.deafen_triggers)
        self.deafen_triggers.pack(anchor=tk.W)
        ttk.Label(tab, foreground="gray", wraplength=540, justify=tk.LEFT, text=(
            "“Muted to everything” is the hold-to-mute state. The other entries match the mute "
            "function configured for the button or fader in GoXLR Utility. Example: leave “Muted to "
            "Stream” unchecked to hide a sound from your stream without muting yourself in Discord."
        )).grid(row=5, column=0, columnspan=3, sticky=tk.W, pady=(10, 0))
        return tab

    def _buttons_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=12)
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, wraplength=540, justify=tk.LEFT, text=(
            "Give a Discord action to a GoXLR button. The button keeps its usual GoXLR function: "
            "an empty sampler slot or an unused effect button works best."
        )).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 12))
        labels = [NO_BUTTON, *ACTION_BUTTONS.values()]
        self.action_vars = {}
        for row, (action, label) in enumerate(BUTTON_ACTIONS.items(), start=1):
            ttk.Label(tab, text=f"{label}:").grid(row=row, column=0, sticky=tk.W, pady=4)
            current = ACTION_BUTTONS.get(self.cfg.button_actions.get(action, ""), NO_BUTTON)
            var = tk.StringVar(value=current)
            ttk.Combobox(tab, textvariable=var, values=labels, state="readonly", width=28
                         ).grid(row=row, column=1, sticky=tk.W, padx=8, pady=4)
            self.action_vars[action] = var
        return tab

    def _button_actions(self) -> dict[str, str] | None:
        by_label = {label: key for key, label in ACTION_BUTTONS.items()}
        chosen = {a: by_label[v.get()] for a, v in self.action_vars.items() if v.get() in by_label}
        if len(set(chosen.values())) != len(chosen):
            messagebox.showerror("Buttons", "Each GoXLR button can only have one action.", parent=self.root)
            return None
        return chosen

    def _behaviour_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=12)
        self.bool_vars = {}
        options = [
            ("cough_sync", "Cough button mutes Discord"),
            ("chat_sync", "Channel fader mute deafens Discord"),
            ("bidirectional", "Two-way sync: muting or deafening in Discord updates the GoXLR"),
            ("notifications", "Show notifications when a connection is lost or restored"),
            ("check_updates", "Check for new versions once a day"),
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
        label = "Start with Windows" if paths.IS_WINDOWS else "Start on login"
        ttk.Checkbutton(tab, text=label, variable=self.autostart_var).pack(anchor=tk.W, pady=2)
        return tab

    def detect(self) -> None:
        url = self.url_var.get().strip()
        self.detect_btn.configure(state=tk.DISABLED)
        self.detect_label.configure(text="Contacting GoXLR Utility…")

        def _done(result):
            self.detect_btn.configure(state=tk.NORMAL)
            if isinstance(result, Exception):
                self.detect_label.configure(text=f"GoXLR Utility not reachable at this address ({result}).")
                return
            if not result:
                self.detect_label.configure(text="GoXLR Utility is running but no GoXLR is connected.")
                return
            self.serial_box.configure(values=[FIRST_DEVICE, *result])
            lines = [f"{serial}: " + ", ".join(f"fader {f} = {c}" for f, c in faders.items())
                     for serial, faders in result.items()]
            self.detect_label.configure(text="\n".join(lines))

        run_in_thread(self.root, lambda: detect_devices(url), _done)

    def save(self) -> None:
        creds = self.credentials
        if creds.client_id or creds.client_secret:
            problem = creds.validate_format()
            if problem:
                messagebox.showerror("Discord application", problem, parent=self.root)
                return
        url = self.url_var.get().strip()
        if not url.startswith(("ws://", "wss://")):
            messagebox.showerror("GoXLR Utility", "The address must start with ws:// (default: "
                                 f"{config.DEFAULT_GOXLR_URL}).", parent=self.root)
            return

        button_actions = self._button_actions()
        if button_actions is None:
            return
        serial = self.serial_var.get().strip()
        cfg = config.Config.from_dict({
            "button_actions": button_actions,
            **self.cfg.to_dict(),
            "client_id": creds.client_id,
            "goxlr_url": url,
            "mixer_serial": "" if serial == FIRST_DEVICE else serial,
            "deafen_channel": self.channel_var.get(),
            "mute_triggers": self.mute_triggers.selected,
            "deafen_triggers": self.deafen_triggers.selected,
            **{name: var.get() for name, var in self.bool_vars.items()},
        })
        try:
            if cfg.client_id != self.cfg.client_id:
                secure_store.set_token(None)
            if creds.client_secret != secure_store.get_client_secret():
                secure_store.set_client_secret(creds.client_secret)
            config.save(cfg)
            if self.autostart_var.get() != autostart.is_enabled():
                (autostart.enable if self.autostart_var.get() else autostart.disable)()
        except OSError as e:
            messagebox.showerror("Settings", f"Could not save the settings:\n{e}", parent=self.root)
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
