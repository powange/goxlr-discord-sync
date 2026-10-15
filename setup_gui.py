"""
GoXLR Discord Sync - Setup wizard

Frozen (GoXLR_Setup.exe): copies the bundled GoXLR_Discord_Sync.exe to the
chosen folder. From source: installs the Python dependencies instead.
Both then configure the Discord application and the autostart.
"""

import argparse
import shutil
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from tkinter import font as tkfont

from goxlr_discord_sync import (
    APP_NAME,
    REPO_URL,
    __version__,
    autostart,
    config,
    migration,
    paths,
    secure_store,
    system,
)
from goxlr_discord_sync.ui_common import DiscordCredentialsFrame, run_in_thread

FROZEN = paths.IS_FROZEN
BUNDLED_EXE = Path(getattr(sys, "_MEIPASS", "")) / paths.APP_EXE_NAME if FROZEN else None
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def stop_running_app() -> None:
    """Stop the installed application so its executable can be replaced or deleted."""
    if not paths.IS_WINDOWS:
        return
    subprocess.run(["taskkill", "/F", "/IM", paths.APP_EXE_NAME], capture_output=True,
                   timeout=15, creationflags=NO_WINDOW, check=False)


def copy_with_retry(source: Path, target: Path, attempts: int = 10) -> None:
    for attempt in range(attempts):
        try:
            shutil.copy2(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)  # the stopped process may still hold the file


def app_installed() -> bool:
    try:
        return autostart.is_enabled() or (paths.default_install_dir() / paths.APP_EXE_NAME).exists()
    except OSError:
        return False


class SetupWizard:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"{APP_NAME} {__version__} – Setup")
        root.geometry("720x620")
        root.minsize(640, 560)

        self.install_dir = paths.default_install_dir() if FROZEN else paths.app_dir()
        self.cfg = config.load()
        self.current = 0
        self.validator = None  # callable(proceed) set by steps that need checks before Next
        self.steps = [self.step_welcome]
        self.steps.append(self.step_directory if FROZEN else self.step_dependencies)
        self.steps += [self.step_discord, self.step_autostart, self.step_complete]

        container = ttk.Frame(root, padding=20)
        container.pack(fill=tk.BOTH, expand=True)
        title_font = tkfont.nametofont("TkDefaultFont").copy()
        title_font.configure(size=16, weight="bold")
        self.title_label = ttk.Label(container, font=title_font)
        self.title_label.pack(side=tk.TOP, anchor=tk.W, pady=(0, 16))
        nav = ttk.Frame(container)
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=(16, 0))
        self.content = ttk.Frame(container)
        self.content.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.back_btn = ttk.Button(nav, text="← Back", command=self.back)
        self.back_btn.pack(side=tk.LEFT)
        self.next_btn = ttk.Button(nav, text="Next →", command=self.next)
        self.next_btn.pack(side=tk.RIGHT)
        self.show()

    # --- navigation -------------------------------------------------------------

    def show(self) -> None:
        for widget in self.content.winfo_children():
            widget.destroy()
        self.validator = None
        self.next_btn.configure(state=tk.NORMAL, text="Next →")
        self.back_btn.configure(state=tk.NORMAL if self.current > 0 else tk.DISABLED)
        self.steps[self.current]()
        if self.current == len(self.steps) - 1:
            self.next_btn.configure(text="Close")
            self.back_btn.configure(state=tk.DISABLED)

    def next(self) -> None:
        if self.current == len(self.steps) - 1:
            self.root.destroy()
            return
        if self.validator is None:
            self._advance()
        else:
            self.validator(self._advance)

    def _advance(self) -> None:
        self.current += 1
        self.show()

    def back(self) -> None:
        if self.current > 0:
            self.current -= 1
            self.show()

    def busy(self, is_busy: bool) -> None:
        state = tk.DISABLED if is_busy else tk.NORMAL
        self.next_btn.configure(state=state)
        self.back_btn.configure(state=state if self.current > 0 else tk.DISABLED)
        self.root.configure(cursor="watch" if is_busy else "")

    def text(self, value: str, **kwargs) -> ttk.Label:
        label = ttk.Label(self.content, text=value, wraplength=640, justify=tk.LEFT, **kwargs)
        label.pack(anchor=tk.W, pady=(0, 10))
        return label

    # --- steps --------------------------------------------------------------------

    def step_welcome(self) -> None:
        self.title_label.configure(text=f"Welcome to {APP_NAME}")
        self.text("This wizard installs and configures GoXLR Discord Sync.\n\n"
                  "What it does:\n"
                  "• Press the Cough button on your GoXLR to mute your microphone in Discord.\n"
                  "• Mute the Chat channel fader to deafen yourself in Discord.\n"
                  "• Optionally, mute or deafen in Discord to update the GoXLR.\n"
                  "• Runs in the system tray and reconnects on its own.")
        requirements = ["GoXLR Utility installed and running", "the Discord desktop app"]
        if not FROZEN:
            requirements.append(f"Python {sys.version_info.major}.{sys.version_info.minor} with pip "
                                f"(this wizard runs on {sys.executable})")
        self.text("You need:\n" + "\n".join(f"• {r}" for r in requirements))
        if app_installed():
            ttk.Separator(self.content).pack(fill=tk.X, pady=10)
            self.text("GoXLR Discord Sync is already installed. Continue to update or reconfigure it, "
                      "or uninstall it.", foreground="#a86100")
            ttk.Button(self.content, text="Uninstall…", command=self.uninstall).pack(anchor=tk.W)

    def step_directory(self) -> None:
        self.title_label.configure(text="Installation folder")
        self.text("Choose where to install the application. Your settings are stored separately "
                  f"in {paths.data_dir()}.")
        row = ttk.Frame(self.content)
        row.pack(fill=tk.X, pady=10)
        dir_var = tk.StringVar(value=str(self.install_dir))
        ttk.Entry(row, textvariable=dir_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        def browse():
            chosen = filedialog.askdirectory(initialdir=dir_var.get(), title="Installation folder")
            if chosen:
                dir_var.set(chosen)

        ttk.Button(row, text="Browse…", command=browse).pack(side=tk.RIGHT)
        status = self.text("")

        def validate(proceed):
            target_dir = Path(dir_var.get().strip())
            if not str(target_dir):
                messagebox.showerror("Installation folder", "Choose a folder.")
                return
            if BUNDLED_EXE is None or not BUNDLED_EXE.exists():
                messagebox.showerror("Setup error", "GoXLR_Discord_Sync.exe is missing from this setup.\n\n"
                                     f"Download a fresh copy from {REPO_URL}/releases")
                return
            self.busy(True)
            status.configure(text="Stopping the running application and copying files…")

            def work():
                target_dir.mkdir(parents=True, exist_ok=True)
                stop_running_app()
                copy_with_retry(BUNDLED_EXE, target_dir / paths.APP_EXE_NAME)
                migration.migrate_legacy_files(target_dir)
                stale = target_dir / "requirements.txt"  # copied by setup 1.x
                if stale.exists():
                    stale.unlink()

            def done(result):
                self.busy(False)
                if isinstance(result, Exception):
                    status.configure(text="")
                    messagebox.showerror("Installation failed", f"Could not install to {target_dir}:\n{result}")
                    return
                self.install_dir = target_dir
                self.cfg = config.load()  # may contain migrated credentials
                proceed()

            run_in_thread(self.root, work, done)

        self.validator = validate

    def step_dependencies(self) -> None:
        self.title_label.configure(text="Python dependencies")
        self.text("Install the Python modules listed in requirements.txt.")
        install_btn = ttk.Button(self.content, text="Install dependencies")
        install_btn.pack(anchor=tk.W)
        log_box = scrolledtext.ScrolledText(self.content, height=14, state=tk.DISABLED)
        log_box.pack(fill=tk.BOTH, expand=True, pady=10)
        progress = ttk.Progressbar(self.content, mode="indeterminate")
        progress.pack(fill=tk.X)

        def append(line: str):
            log_box.configure(state=tk.NORMAL)
            log_box.insert(tk.END, line)
            log_box.see(tk.END)
            log_box.configure(state=tk.DISABLED)

        def start():
            requirements = paths.app_dir() / "requirements.txt"
            if not requirements.exists():
                messagebox.showerror("Dependencies", f"requirements.txt not found in {requirements.parent}")
                return
            install_btn.configure(state=tk.DISABLED)
            self.busy(True)
            progress.start()

            def work(emit):
                process = subprocess.Popen(
                    [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    cwd=str(requirements.parent), creationflags=NO_WINDOW,
                )
                for line in process.stdout:
                    emit(line)
                return process.wait()

            def done(result):
                progress.stop()
                self.busy(False)
                install_btn.configure(state=tk.NORMAL)
                if result == 0:
                    append("\n✓ Dependencies installed.\n")
                else:
                    append(f"\n✗ Installation failed ({result}).\n")
                    messagebox.showerror("Dependencies", "pip failed. Read the log above, fix the problem and retry.")

            run_in_thread(self.root, work, done, on_progress=append)

        install_btn.configure(command=start)

    def step_discord(self) -> None:
        self.title_label.configure(text="Discord application")
        self.text("Discord only lets an application control your voice settings if you created it "
                  "yourself. It takes two minutes:")
        form = DiscordCredentialsFrame(self.content, self.cfg.redirect_uri, self.cfg.client_id,
                                       secure_store.get_client_secret())
        form.pack(fill=tk.X)

        def save_and_proceed(proceed):
            try:
                if form.client_id != self.cfg.client_id:
                    secure_store.set_token(None)
                if form.client_secret != secure_store.get_client_secret():
                    secure_store.set_client_secret(form.client_secret)
                self.cfg.client_id = form.client_id
                config.save(self.cfg)
            except OSError as e:
                messagebox.showerror("Discord application", f"Could not save the settings:\n{e}")
                return
            proceed()

        def validate(proceed):
            problem = form.validate_format()
            if problem:
                messagebox.showerror("Discord application", problem)
                return
            if form.verified:
                save_and_proceed(proceed)
                return

            def checked(ok):
                if ok or messagebox.askyesno(
                    "Discord application",
                    "These values could not be confirmed with Discord.\n\nSave them anyway?",
                ):
                    save_and_proceed(proceed)

            form.check(on_done=checked)

        self.validator = validate

    def step_autostart(self) -> None:
        self.title_label.configure(text="Start automatically")
        enabled = tk.BooleanVar(value=True)
        label = "Start GoXLR Discord Sync with Windows" if paths.IS_WINDOWS else "Start GoXLR Discord Sync on login"
        ttk.Checkbutton(self.content, text=label, variable=enabled).pack(anchor=tk.W, pady=10)
        self.text("You can change this later from the tray icon menu.", foreground="gray")

        def validate(proceed):
            try:
                if enabled.get():
                    autostart.enable(self.launch_command())
                else:
                    autostart.disable()
            except OSError as e:
                messagebox.showerror("Autostart", f"Could not change the autostart setting:\n{e}")
                return
            proceed()

        self.validator = validate

    def step_complete(self) -> None:
        self.title_label.configure(text="Setup complete")
        self.text("GoXLR Discord Sync is installed.\n\n"
                  "On first launch, Discord asks you to authorize the application: click “Authorize” "
                  "in the Discord window.\n\n"
                  "The tray icon shows the state: green = mic live, red = muted or deafened, "
                  "orange = waiting for GoXLR Utility or Discord, grey = action needed. "
                  "Right-click it for options.")
        ttk.Button(self.content, text="Launch now", command=self.launch).pack(anchor=tk.W, pady=10)

    # --- actions ----------------------------------------------------------------------

    def launch_command(self) -> list[str]:
        if FROZEN:
            return [str(self.install_dir / paths.APP_EXE_NAME)]
        return paths.self_command()

    def launch(self) -> None:
        try:
            system.launch_detached(self.launch_command())
        except OSError as e:
            messagebox.showerror("Launch", f"Could not start the application:\n{e}")
            return
        messagebox.showinfo("Launched", "GoXLR Discord Sync is running. Look for its icon in the system tray.")

    def uninstall(self) -> None:
        if not messagebox.askyesno("Uninstall", "Stop GoXLR Discord Sync, remove the autostart and delete "
                                   "the application?"):
            return
        remove_settings = messagebox.askyesno(
            "Uninstall", f"Also delete your settings and Discord credentials?\n\n({paths.data_dir()})")
        report = []
        try:
            exe = autostart.installed_exe_path() or (paths.default_install_dir() / paths.APP_EXE_NAME)
            stop_running_app()
            autostart.disable()
            report.append("✓ Autostart removed")
            if FROZEN and exe.name.lower() == paths.APP_EXE_NAME.lower():
                for _ in range(10):
                    try:
                        exe.unlink(missing_ok=True)
                        break
                    except PermissionError:
                        time.sleep(0.5)
                (exe.parent / "requirements.txt").unlink(missing_ok=True)
                try:
                    exe.parent.rmdir()  # only when empty
                except OSError:
                    pass
                report.append(f"✓ Application removed from {exe.parent}")
            if remove_settings:
                secure_store.clear_all()
                shutil.rmtree(paths.data_dir(), ignore_errors=True)
                report.append("✓ Settings and credentials deleted")
            else:
                report.append(f"Settings kept in {paths.data_dir()}")
        except OSError as e:
            report.append(f"⚠ {e}")
        messagebox.showinfo("Uninstall", "\n".join(report))
        self.root.destroy()


class UpdateWindow:
    """Unattended update started by the application (--update): replace the exe, start it again."""

    def __init__(self, root: tk.Tk, target: Path):
        self.root = root
        self.target = target
        root.title(f"{APP_NAME} – Update")
        root.resizable(False, False)
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text=f"Updating {APP_NAME} to version {__version__}…").pack(anchor=tk.W)
        self.status = ttk.Label(frame, text="Waiting for the application to close…", foreground="gray")
        self.status.pack(anchor=tk.W, pady=(6, 12))
        progress = ttk.Progressbar(frame, mode="indeterminate", length=360)
        progress.pack(fill=tk.X)
        progress.start()
        run_in_thread(root, self.work, self.done, on_progress=lambda text: self.status.configure(text=text))

    def work(self, emit) -> None:
        if BUNDLED_EXE is None or not BUNDLED_EXE.exists():
            raise RuntimeError("This installer does not contain the application")
        try:
            copy_with_retry(BUNDLED_EXE, self.target, attempts=20)  # the app quits by itself
        except PermissionError:
            emit("Stopping the application…")
            stop_running_app()
            copy_with_retry(BUNDLED_EXE, self.target)
        emit("Starting the new version…")
        system.launch_detached([str(self.target)])

    def done(self, result) -> None:
        if isinstance(result, Exception):
            messagebox.showerror(
                "Update failed",
                f"The update could not be installed:\n{result}\n\n"
                f"Run GoXLR_Setup.exe again, or download it from {REPO_URL}/releases",
                parent=self.root,
            )
        self.root.destroy()


def update_target(value: str) -> Path:
    target = Path(value)
    if target.name.lower() != paths.APP_EXE_NAME.lower():
        raise argparse.ArgumentTypeError(f"--target must point to {paths.APP_EXE_NAME}")
    return target


def main(argv=None) -> None:
    system.fix_std_streams()
    parser = argparse.ArgumentParser(prog="GoXLR_Setup")
    parser.add_argument("--update", action="store_true", help="replace the installed application and restart it")
    parser.add_argument("--target", type=update_target, help="installed GoXLR_Discord_Sync.exe (with --update)")
    args = parser.parse_args(argv)
    root = tk.Tk()
    if args.update:
        target = args.target or paths.default_install_dir() / paths.APP_EXE_NAME
        UpdateWindow(root, target)
    else:
        SetupWizard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
