"""Tkinter pieces shared by the setup wizard and the settings window."""

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from . import auth
from .i18n import tr

PORTAL_URL = "https://discord.com/developers/applications"

def instructions() -> str:
    return "\n".join([
        tr("1. Open the Discord Developer Portal and click “New Application”."),
        tr("2. Copy the Application ID (Client ID) from “General Information”."),
        tr("3. In “OAuth2”, reset and copy the Client Secret."),
        tr("4. In “OAuth2 › Redirects”, add the redirect URL below, then save."),
        tr("5. Paste the Client ID and Client Secret here and click “Check”."),
    ])


def run_in_thread(widget: tk.Misc, work, done, on_progress=None, poll_ms: int = 50) -> None:
    """Run work() in a thread and call done(result) in the Tk thread.

    Tk must only be touched from its own thread, so the worker never calls Tk:
    it puts messages in a queue that the Tk thread polls. With on_progress,
    work receives an emit(value) function whose values reach on_progress.
    """
    messages: queue.Queue = queue.Queue()

    def _target():
        try:
            result = work(lambda value: messages.put(("progress", value))) if on_progress else work()
        except Exception as e:  # reported to the UI
            result = e
        messages.put(("done", result))

    def _poll():
        try:
            while True:
                kind, value = messages.get_nowait()
                if kind == "progress":
                    on_progress(value)
                else:
                    done(value)
                    return
        except queue.Empty:
            pass
        try:
            widget.after(poll_ms, _poll)
        except tk.TclError:
            pass  # window closed meanwhile

    threading.Thread(target=_target, daemon=True).start()
    widget.after(poll_ms, _poll)


class DiscordCredentialsFrame(ttk.Frame):
    """Client ID / secret form with portal link, redirect URL copy and a live check."""

    def __init__(self, parent, redirect_uri: str, client_id: str = "", client_secret: str = "",
                 show_instructions: bool = True):
        super().__init__(parent)
        self.redirect_uri = redirect_uri
        self.verified = False
        self.columnconfigure(1, weight=1)

        row = 0
        if show_instructions:
            ttk.Label(self, text=instructions(), justify=tk.LEFT).grid(row=row, column=0, columnspan=3,
                                                                     sticky=tk.W, pady=(0, 8))
            row += 1
            ttk.Button(self, text=tr("Open Discord Developer Portal"),
                       command=lambda: webbrowser.open(PORTAL_URL)).grid(row=row, column=0, columnspan=3,
                                                                          sticky=tk.W, pady=(0, 10))
            row += 1

        ttk.Label(self, text=tr("Redirect URL:")).grid(row=row, column=0, sticky=tk.W, pady=3)
        redirect = ttk.Entry(self)
        redirect.insert(0, redirect_uri)
        redirect.configure(state="readonly")
        redirect.grid(row=row, column=1, sticky=tk.EW, padx=8, pady=3)
        self.copy_btn = ttk.Button(self, text=tr("Copy"), command=self._copy_redirect)
        self.copy_btn.grid(row=row, column=2, sticky=tk.EW, pady=3)
        row += 1

        ttk.Label(self, text="Client ID:").grid(row=row, column=0, sticky=tk.W, pady=3)
        self.client_id_var = tk.StringVar(value=client_id)
        ttk.Entry(self, textvariable=self.client_id_var).grid(row=row, column=1, columnspan=2,
                                                              sticky=tk.EW, padx=(8, 0), pady=3)
        row += 1

        ttk.Label(self, text="Client Secret:").grid(row=row, column=0, sticky=tk.W, pady=3)
        self.secret_var = tk.StringVar(value=client_secret)
        self.secret_entry = ttk.Entry(self, textvariable=self.secret_var, show="•")
        self.secret_entry.grid(row=row, column=1, sticky=tk.EW, padx=8, pady=3)
        self.show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text=tr("Show"), variable=self.show_var,
                        command=lambda: self.secret_entry.configure(show="" if self.show_var.get() else "•")
                        ).grid(row=row, column=2, sticky=tk.W, pady=3)
        row += 1

        check_row = ttk.Frame(self)
        check_row.grid(row=row, column=0, columnspan=3, sticky=tk.EW, pady=(8, 0))
        self.check_btn = ttk.Button(check_row, text=tr("Check"), command=self.check)
        self.check_btn.pack(side=tk.LEFT)
        self.result_label = ttk.Label(check_row, text="", wraplength=440, justify=tk.LEFT)
        self.result_label.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)

        for var in (self.client_id_var, self.secret_var):
            var.trace_add("write", lambda *_: self._invalidate())

    @property
    def client_id(self) -> str:
        return self.client_id_var.get().strip()

    @property
    def client_secret(self) -> str:
        return self.secret_var.get().strip()

    def _invalidate(self) -> None:
        self.verified = False
        self.result_label.configure(text="", foreground="")

    def _copy_redirect(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.redirect_uri)
        self.copy_btn.configure(text=tr("Copied"))
        self.after(1500, lambda: self.copy_btn.configure(text=tr("Copy")))

    def validate_format(self) -> str | None:
        if not self.client_id or not self.client_secret:
            return tr("Enter both the Client ID and the Client Secret.")
        if not self.client_id.isdigit():
            return tr("The Client ID is a number (the “Application ID” in the Developer Portal).")
        return None

    def check(self, on_done=None) -> None:
        problem = self.validate_format()
        if problem:
            self.result_label.configure(text=problem, foreground="#b8322a")
            return
        self.check_btn.configure(state=tk.DISABLED)
        self.result_label.configure(text=tr("Checking with Discord…"), foreground="")
        client_id, secret = self.client_id, self.client_secret

        def _done(result):
            self.check_btn.configure(state=tk.NORMAL)
            if isinstance(result, Exception):
                ok, message = False, tr("Check failed: {error}", error=result)
            else:
                ok, message = result
            self.verified = ok
            self.result_label.configure(text=message, foreground="#2c7a4b" if ok else "#b8322a")
            if on_done:
                on_done(ok)

        run_in_thread(self, lambda: auth.check_credentials(client_id, secret), _done)
