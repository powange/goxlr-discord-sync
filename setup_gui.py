"""
GoXLR Discord Sync - Setup GUI
Interactive installation and configuration wizard
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import subprocess
import threading
import os
import sys
import webbrowser

# Global flag to track if app was already installed before setup started
APP_ALREADY_INSTALLED = False

# Detect if running as compiled exe or script
if getattr(sys, 'frozen', False):
    # Running as compiled exe - use exe directory
    SCRIPT_DIR = os.path.dirname(sys.executable)

    # Check if app is already installed by checking auto-start (better indicator than just exe)
    startup_folder = os.path.join(os.getenv('APPDATA'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
    autostart_vbs = os.path.join(startup_folder, 'GoXLR_Discord_Sync.vbs')
    APP_ALREADY_INSTALLED = os.path.exists(autostart_vbs)

    print(f"DEBUG: SCRIPT_DIR = {SCRIPT_DIR}")
    print(f"DEBUG: autostart_vbs = {autostart_vbs}")
    print(f"DEBUG: APP_ALREADY_INSTALLED = {APP_ALREADY_INSTALLED}")

    # Store bundled file paths for later extraction (after directory is chosen)
    BUNDLED_EXE_PATH = os.path.join(sys._MEIPASS, 'GoXLR_Discord_Sync.exe')
    BUNDLED_REQ_PATH = os.path.join(sys._MEIPASS, 'requirements.txt')
else:
    # Running as script
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLED_EXE_PATH = None
    BUNDLED_REQ_PATH = None

# Default installation directory (only used when running as exe)
DEFAULT_INSTALL_DIR = os.path.join(os.getenv('LOCALAPPDATA'), 'GoXLR Discord Sync')
INSTALL_DIR = SCRIPT_DIR  # Will be updated in the wizard if needed

CLIENT_ID_FILE = os.path.join(SCRIPT_DIR, "client_id.txt")
SECRET_FILE = os.path.join(SCRIPT_DIR, "client_secret.txt")
REDIRECT_PORT = 9543

class SetupWizard:
    def __init__(self, root):
        self.root = root
        self.root.title("GoXLR Discord Sync - Setup Wizard")
        self.root.geometry("700x600")
        self.root.resizable(True, True)

        self.current_step = 0
        self.install_dir = DEFAULT_INSTALL_DIR if getattr(sys, 'frozen', False) else SCRIPT_DIR

        # If running as exe, skip dependency installation (not needed for exe)
        if getattr(sys, 'frozen', False):
            self.steps = [
                self.step_welcome,
                self.step_choose_directory,
                self.step_discord_app,
                self.step_autostart,
                self.step_complete
            ]
        else:
            # Running as script - need to install dependencies
            self.steps = [
                self.step_welcome,
                self.step_install_dependencies,
                self.step_discord_app,
                self.step_autostart,
                self.step_complete
            ]

        # Main container
        self.container = ttk.Frame(root, padding="20")
        self.container.pack(fill=tk.BOTH, expand=True)

        # Title
        self.title_label = ttk.Label(
            self.container,
            text="",
            font=("Arial", 16, "bold")
        )
        self.title_label.pack(side=tk.TOP, pady=(0, 20))

        # Navigation buttons (pack first at bottom to reserve space)
        self.nav_frame = ttk.Frame(self.container)
        self.nav_frame.pack(side=tk.BOTTOM, pady=(20, 0), fill=tk.X)

        # Content frame (fills remaining space)
        self.content_frame = ttk.Frame(self.container)
        self.content_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.back_btn = ttk.Button(
            self.nav_frame,
            text="← Back",
            command=self.prev_step,
            state=tk.DISABLED
        )
        self.back_btn.pack(side=tk.LEFT)

        self.next_btn = ttk.Button(
            self.nav_frame,
            text="Next →",
            command=self.next_step
        )
        self.next_btn.pack(side=tk.RIGHT)

        # Show first step
        self.show_step()

    def clear_content(self):
        for widget in self.content_frame.winfo_children():
            widget.destroy()

    def show_step(self):
        self.clear_content()

        # Reset next button state (enable by default)
        self.next_btn.config(state=tk.NORMAL)

        # Execute step
        self.steps[self.current_step]()

        # Update navigation buttons
        self.back_btn.config(state=tk.NORMAL if self.current_step > 0 else tk.DISABLED)

        if self.current_step == len(self.steps) - 1:
            self.next_btn.config(text="Finish", command=self.finish)
        else:
            self.next_btn.config(text="Next →", command=self.next_step)

    def next_step(self):
        if self.current_step < len(self.steps) - 1:
            self.current_step += 1
            self.show_step()

    def prev_step(self):
        if self.current_step > 0:
            self.current_step -= 1
            self.show_step()

    def finish(self):
        self.root.destroy()

    # Step 1: Welcome
    def step_welcome(self):
        self.title_label.config(text="Welcome to GoXLR Discord Sync")

        ttk.Label(
            self.content_frame,
            text="This wizard will help you install and configure GoXLR Discord Sync.",
            wraplength=600,
            font=("Arial", 11)
        ).pack(pady=20)

        # Show installation directory
        ttk.Label(
            self.content_frame,
            text=f"Installation directory:\n{SCRIPT_DIR}",
            font=("Arial", 9),
            foreground="gray"
        ).pack(pady=10)

        # Check if already installed (using the flag set at startup)
        print(f"DEBUG in step_welcome: APP_ALREADY_INSTALLED = {APP_ALREADY_INSTALLED}")
        if APP_ALREADY_INSTALLED:
            ttk.Label(
                self.content_frame,
                text="⚠️ GoXLR Discord Sync is already installed",
                font=("Arial", 10, "bold"),
                foreground="orange"
            ).pack(pady=20)

            ttk.Button(
                self.content_frame,
                text="🗑️ Uninstall",
                command=self.uninstall_app
            ).pack(pady=10)

        info_text = """
What this does:
• Synchronizes your GoXLR Cough button with Discord mute
• Runs in the background with a system tray icon
• Auto-reconnects if Discord or GoXLR Utility restarts
• Starts automatically with Windows

Requirements:
• GoXLR Utility must be installed and running
• Discord desktop app must be running
• Python 3.x with pip installed

This wizard will:
1. Install required Python modules
2. Help you create a Discord application
3. Build a standalone executable
4. Set up auto-start with Windows
        """

        ttk.Label(
            self.content_frame,
            text=info_text,
            justify=tk.LEFT,
            font=("Arial", 10)
        ).pack(pady=20)

    # Step 2: Choose installation directory (only for exe mode)
    def step_choose_directory(self):
        from tkinter import filedialog

        self.title_label.config(text="Choose Installation Directory")

        ttk.Label(
            self.content_frame,
            text="Select where to install GoXLR Discord Sync:",
            font=("Arial", 11)
        ).pack(pady=20)

        # Frame for directory selection
        dir_frame = ttk.Frame(self.content_frame)
        dir_frame.pack(pady=20, fill=tk.X, padx=20)

        self.dir_entry = ttk.Entry(dir_frame, font=("Arial", 10))
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.dir_entry.insert(0, self.install_dir)

        def browse_directory():
            directory = filedialog.askdirectory(
                initialdir=self.install_dir,
                title="Select Installation Directory"
            )
            if directory:
                self.dir_entry.delete(0, tk.END)
                self.dir_entry.insert(0, directory)

        ttk.Button(
            dir_frame,
            text="Browse...",
            command=browse_directory
        ).pack(side=tk.RIGHT)

        # Info text
        ttk.Label(
            self.content_frame,
            text="The application will be installed in this directory.\n"
                 "Configuration files (client_id.txt, etc.) will also be stored here.",
            font=("Arial", 9),
            foreground="gray",
            justify=tk.LEFT
        ).pack(pady=10, padx=20)

        # Override next button to validate and save directory
        def on_next():
            selected_dir = self.dir_entry.get().strip()
            if not selected_dir:
                messagebox.showerror("Error", "Please select an installation directory.")
                return

            self.install_dir = selected_dir

            # Create directory if it doesn't exist
            try:
                os.makedirs(self.install_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Error", f"Could not create directory:\n{e}")
                return

            # Update global paths
            global INSTALL_DIR, CLIENT_ID_FILE, SECRET_FILE
            INSTALL_DIR = self.install_dir
            CLIENT_ID_FILE = os.path.join(INSTALL_DIR, "client_id.txt")
            SECRET_FILE = os.path.join(INSTALL_DIR, "client_secret.txt")

            # Extract bundled files to the chosen directory
            import shutil
            extracted_files = []

            if BUNDLED_EXE_PATH and os.path.exists(BUNDLED_EXE_PATH):
                try:
                    target_exe = os.path.join(self.install_dir, 'GoXLR_Discord_Sync.exe')
                    shutil.copy2(BUNDLED_EXE_PATH, target_exe)
                    extracted_files.append("GoXLR_Discord_Sync.exe")
                    print(f"Extracted GoXLR_Discord_Sync.exe to {target_exe}")
                except Exception as e:
                    messagebox.showerror("Error", f"Could not extract executable:\n{e}")
                    return
            else:
                print(f"DEBUG: BUNDLED_EXE_PATH = {BUNDLED_EXE_PATH}")
                print(f"DEBUG: exists = {os.path.exists(BUNDLED_EXE_PATH) if BUNDLED_EXE_PATH else 'N/A'}")
                messagebox.showwarning("Warning", f"GoXLR_Discord_Sync.exe not found in setup bundle.\n\nBUNDLED_EXE_PATH={BUNDLED_EXE_PATH}\nexists={os.path.exists(BUNDLED_EXE_PATH) if BUNDLED_EXE_PATH else 'N/A'}")

            if BUNDLED_REQ_PATH and os.path.exists(BUNDLED_REQ_PATH):
                try:
                    target_req = os.path.join(self.install_dir, 'requirements.txt')
                    shutil.copy2(BUNDLED_REQ_PATH, target_req)
                    extracted_files.append("requirements.txt")
                    print(f"Extracted requirements.txt to {target_req}")
                except Exception as e:
                    print(f"Warning: Could not extract requirements.txt: {e}")

            if extracted_files:
                print(f"Successfully extracted: {', '.join(extracted_files)}")

            self.next_step()

        # Remove default next button and add custom one
        for widget in self.nav_frame.winfo_children():
            widget.destroy()

        # Recreate navigation buttons with custom next handler
        self.back_btn = ttk.Button(
            self.nav_frame,
            text="← Back",
            command=self.prev_step,
            state=tk.NORMAL if self.current_step > 0 else tk.DISABLED
        )
        self.back_btn.pack(side=tk.LEFT)

        self.next_btn = ttk.Button(
            self.nav_frame,
            text="Next",
            command=on_next
        )
        self.next_btn.pack(side=tk.RIGHT)

    # Step 3: Install dependencies
    def step_install_dependencies(self):
        self.title_label.config(text="Installing Dependencies")

        ttk.Label(
            self.content_frame,
            text="Click 'Install' to install required Python modules.",
            font=("Arial", 11)
        ).pack(pady=20)

        # Install button
        self.install_btn = ttk.Button(
            self.content_frame,
            text="📦 Install Dependencies",
            command=self.start_install
        )
        self.install_btn.pack(pady=10)

        # Progress text
        self.install_log = scrolledtext.ScrolledText(
            self.content_frame,
            height=15,
            width=80,
            state=tk.DISABLED
        )
        self.install_log.pack(pady=10, fill=tk.BOTH, expand=True)

        self.install_progress = ttk.Progressbar(
            self.content_frame,
            mode='indeterminate'
        )
        self.install_progress.pack(fill=tk.X, pady=10)

    def start_install(self):
        # Prevent multiple clicks
        self.install_btn.config(state=tk.DISABLED, text="Installing...")
        self.next_btn.config(state=tk.DISABLED)
        self.back_btn.config(state=tk.DISABLED)

        self.log_install("Starting installation...\n")
        self.log_install(f"Running from: {sys.executable}\n")
        self.log_install(f"Script dir: {SCRIPT_DIR}\n\n")

        # Start installation in thread
        threading.Thread(target=self.install_dependencies, daemon=True).start()

    def install_dependencies(self):
        self.install_progress.start()
        self.log_install("Installing dependencies from requirements.txt...\n")

        # Check if requirements.txt exists (try multiple locations)
        requirements_path = os.path.join(SCRIPT_DIR, "requirements.txt")

        # If running from dist folder, also check parent
        if not os.path.exists(requirements_path):
            parent_req = os.path.join(os.path.dirname(SCRIPT_DIR), "requirements.txt")
            if os.path.exists(parent_req):
                requirements_path = parent_req

        if not os.path.exists(requirements_path):
            self.log_install(f"\n✗ Error: requirements.txt not found\n")
            self.log_install(f"Searched in: {SCRIPT_DIR}\n")
            self.log_install("Please make sure requirements.txt is in the same folder as the setup.\n")
            self.install_progress.stop()
            self.root.after(0, lambda: messagebox.showerror(
                "Error",
                "requirements.txt not found!\n\nMake sure it's in the same folder as the setup."
            ))
            return

        self.log_install(f"Requirements file: {requirements_path}\n")
        self.log_install(f"Python executable: {sys.executable}\n")
        self.log_install(f"Working directory: {SCRIPT_DIR}\n\n")

        try:
            # Don't use shell=True to avoid triggering shell scripts
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "-r", requirements_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=SCRIPT_DIR,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )

            for line in process.stdout:
                self.log_install(line)

            process.wait()

            if process.returncode == 0:
                self.log_install("\n✓ Installation complete!\n")
                self.root.after(0, lambda: self.next_btn.config(state=tk.NORMAL))
            else:
                self.log_install("\n✗ Installation failed!\n")
                self.root.after(0, lambda: messagebox.showerror(
                    "Error",
                    "Failed to install dependencies. Please check the log."
                ))
        except Exception as e:
            self.log_install(f"\n✗ Error: {e}\n")
        finally:
            self.install_progress.stop()

    def log_install(self, text):
        self.root.after(0, lambda: self._append_log(text))

    def _append_log(self, text):
        self.install_log.config(state=tk.NORMAL)
        self.install_log.insert(tk.END, text)
        self.install_log.see(tk.END)
        self.install_log.config(state=tk.DISABLED)

    # Step 3: Discord app configuration
    def step_discord_app(self):
        self.title_label.config(text="Discord Application Setup")

        # Check if already configured (use self.install_dir for correct path)
        client_id_file = os.path.join(self.install_dir, "client_id.txt")
        secret_file = os.path.join(self.install_dir, "client_secret.txt")

        if os.path.exists(client_id_file) and os.path.exists(secret_file):
            with open(client_id_file, 'r') as f:
                client_id = f.read().strip()
            with open(secret_file, 'r') as f:
                client_secret = f.read().strip()

            if client_id and client_secret:
                ttk.Label(
                    self.content_frame,
                    text="✓ Discord application already configured!",
                    font=("Arial", 11),
                    foreground="green"
                ).pack(pady=20)

                # Show configured info (partially hidden)
                info_frame = ttk.Frame(self.content_frame)
                info_frame.pack(pady=20)

                ttk.Label(info_frame, text="Client ID:", font=("Arial", 9)).grid(
                    row=0, column=0, sticky=tk.W, padx=5
                )
                ttk.Label(info_frame, text=client_id[:10] + "..." + client_id[-5:],
                         font=("Arial", 9), foreground="gray").grid(
                    row=0, column=1, sticky=tk.W, padx=5
                )

                ttk.Label(info_frame, text="Client Secret:", font=("Arial", 9)).grid(
                    row=1, column=0, sticky=tk.W, padx=5
                )
                ttk.Label(info_frame, text="*" * 20,
                         font=("Arial", 9), foreground="gray").grid(
                    row=1, column=1, sticky=tk.W, padx=5
                )

                ttk.Label(
                    self.content_frame,
                    text="Click 'Next' to continue or 'Reconfigure' to change settings.",
                    font=("Arial", 10)
                ).pack(pady=10)

                ttk.Button(
                    self.content_frame,
                    text="Reconfigure",
                    command=self.show_discord_form
                ).pack(pady=10)

                # Enable next button since config exists
                self.next_btn.config(state=tk.NORMAL)
                return

        # No config found, show form
        self.show_discord_form()

    def show_discord_form(self):
        self.clear_content()

        ttk.Label(
            self.content_frame,
            text="Create a Discord Application:",
            font=("Arial", 11, "bold")
        ).pack(pady=(0, 10))

        instructions = f"""
1. Click the button below to open Discord Developer Portal
2. Click "New Application" and give it a name (e.g., "GoXLR Sync")
3. Copy the "Application ID" (Client ID)
4. Go to "OAuth2" tab and copy the "Client Secret"
5. In "OAuth2 > Redirects", add this URL:
   http://127.0.0.1:{REDIRECT_PORT}/callback
6. Click "Save Changes"
7. Paste your Client ID and Secret below
        """

        ttk.Label(
            self.content_frame,
            text=instructions,
            justify=tk.LEFT,
            font=("Arial", 9)
        ).pack(pady=10)

        ttk.Button(
            self.content_frame,
            text="🌐 Open Discord Developer Portal",
            command=lambda: webbrowser.open("https://discord.com/developers/applications")
        ).pack(pady=10)

        ttk.Separator(self.content_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=20)

        # Form
        form_frame = ttk.Frame(self.content_frame)
        form_frame.pack(fill=tk.X, padx=50)

        ttk.Label(form_frame, text="Client ID:", font=("Arial", 10)).grid(
            row=0, column=0, sticky=tk.W, pady=5
        )
        self.client_id_entry = ttk.Entry(form_frame, width=50)
        self.client_id_entry.grid(row=0, column=1, pady=5, padx=(10, 0))

        ttk.Label(form_frame, text="Client Secret:", font=("Arial", 10)).grid(
            row=1, column=0, sticky=tk.W, pady=5
        )
        self.client_secret_entry = ttk.Entry(form_frame, width=50, show="*")
        self.client_secret_entry.grid(row=1, column=1, pady=5, padx=(10, 0))

        ttk.Button(
            self.content_frame,
            text="Save Configuration",
            command=self.save_discord_config
        ).pack(pady=20)

        # Load existing if available (use self.install_dir)
        client_id_file = os.path.join(self.install_dir, "client_id.txt")
        secret_file = os.path.join(self.install_dir, "client_secret.txt")

        if os.path.exists(client_id_file):
            with open(client_id_file, 'r') as f:
                self.client_id_entry.insert(0, f.read().strip())
        if os.path.exists(secret_file):
            with open(secret_file, 'r') as f:
                self.client_secret_entry.insert(0, f.read().strip())

    def save_discord_config(self):
        client_id = self.client_id_entry.get().strip()
        client_secret = self.client_secret_entry.get().strip()

        if not client_id or not client_secret:
            messagebox.showerror("Error", "Please fill in both fields!")
            return

        try:
            # Ensure install directory exists
            os.makedirs(self.install_dir, exist_ok=True)

            # Save to install directory
            client_id_file = os.path.join(self.install_dir, "client_id.txt")
            secret_file = os.path.join(self.install_dir, "client_secret.txt")

            with open(client_id_file, 'w') as f:
                f.write(client_id)
            with open(secret_file, 'w') as f:
                f.write(client_secret)

            # Update global variables for compatibility
            global CLIENT_ID_FILE, SECRET_FILE
            CLIENT_ID_FILE = client_id_file
            SECRET_FILE = secret_file

            messagebox.showinfo("Success", "Configuration saved successfully!")
            self.next_step()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save configuration: {e}")

    # Step 4: Build executable
    def step_build(self):
        self.title_label.config(text="Build Executable")

        ttk.Label(
            self.content_frame,
            text="Build a standalone executable for better performance and notifications.",
            font=("Arial", 11)
        ).pack(pady=20)

        # Check if exe exists (extracted or pre-existing)
        exe_path = os.path.join(SCRIPT_DIR, "GoXLR_Discord_Sync.exe")
        exe_exists = os.path.exists(exe_path)

        if exe_exists:
            ttk.Label(
                self.content_frame,
                text="✓ Executable already exists!",
                font=("Arial", 11),
                foreground="green"
            ).pack(pady=10)

            ttk.Label(
                self.content_frame,
                text=f"Location: {exe_path}",
                font=("Arial", 9)
            ).pack()

            ttk.Label(
                self.content_frame,
                text="You can skip this step or rebuild.",
                font=("Arial", 10)
            ).pack(pady=10)

            # Enable Next button since exe exists
            self.next_btn.config(state=tk.NORMAL)
        else:
            ttk.Label(
                self.content_frame,
                text="This may take a few minutes...",
                font=("Arial", 9),
                foreground="gray"
            ).pack(pady=10)

        # Build button
        self.build_btn = ttk.Button(
            self.content_frame,
            text="🔨 Build Executable",
            command=self.start_build
        )
        self.build_btn.pack(pady=20)

        # Build log
        self.build_log = scrolledtext.ScrolledText(
            self.content_frame,
            height=10,
            width=80,
            state=tk.DISABLED
        )
        self.build_log.pack(pady=10, fill=tk.BOTH, expand=True)

        self.build_progress = ttk.Progressbar(
            self.content_frame,
            mode='indeterminate'
        )
        self.build_progress.pack(fill=tk.X, pady=10)

        # Status label
        self.build_status = ttk.Label(
            self.content_frame,
            text="",
            font=("Arial", 11)
        )
        self.build_status.pack(pady=10)

    def start_build(self):
        self.build_btn.config(state=tk.DISABLED)
        self.next_btn.config(state=tk.DISABLED)
        threading.Thread(target=self.build_executable, daemon=True).start()

    def build_executable(self):
        self.build_progress.start()
        self.log_build("Starting build process...\n")

        # Check if source file exists
        source_file = os.path.join(SCRIPT_DIR, "goxlr_discord_sync.pyw")
        if not os.path.exists(source_file):
            self.log_build(f"\n✗ Error: goxlr_discord_sync.pyw not found at {source_file}\n")
            self.log_build("Please make sure the setup is in the correct folder.\n")
            self.build_progress.stop()
            self.root.after(0, lambda: messagebox.showerror(
                "Error",
                "goxlr_discord_sync.pyw not found!\n\nPlease place the setup in the project folder."
            ))
            self.root.after(0, lambda: self.next_btn.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.build_btn.config(state=tk.NORMAL))
            return

        try:
            process = subprocess.Popen(
                [
                    sys.executable, "-m", "PyInstaller",
                    "--onefile", "--windowed",
                    "--name", "GoXLR_Discord_Sync",
                    "--icon=NONE",
                    "goxlr_discord_sync.pyw"
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=SCRIPT_DIR
            )

            for line in process.stdout:
                self.log_build(line)

            process.wait()

            exe_path = os.path.join(SCRIPT_DIR, "GoXLR_Discord_Sync.exe")
            if process.returncode == 0 and os.path.exists(exe_path):
                self.log_build("\n✓ Build complete!\n")
                self.root.after(0, self._on_build_success)
            else:
                self.log_build("\n✗ Build failed!\n")
                self.root.after(0, self._on_build_error)
                self.root.after(0, lambda: self.next_btn.config(state=tk.NORMAL))
        except Exception as e:
            self.log_build(f"\n✗ Error: {e}\n")
            self.root.after(0, lambda: self.next_btn.config(state=tk.NORMAL))
        finally:
            self.build_progress.stop()
            self.root.after(0, lambda: self.build_btn.config(state=tk.NORMAL))

    def _on_build_success(self):
        self.build_status.config(text="✓ Build successful! Click Next to continue.", foreground="green")
        self.next_btn.config(state=tk.NORMAL)
        self.root.update_idletasks()

    def _on_build_error(self):
        self.build_status.config(text="⚠ Build failed. You can skip this step.", foreground="orange")
        messagebox.showerror(
            "Error",
            "Failed to build executable. You can still use the Python script."
        )

    def log_build(self, text):
        self.root.after(0, lambda: self._append_build_log(text))

    def _append_build_log(self, text):
        self.build_log.config(state=tk.NORMAL)
        self.build_log.insert(tk.END, text)
        self.build_log.see(tk.END)
        self.build_log.config(state=tk.DISABLED)

    # Step 5: Auto-start setup
    def step_autostart(self):
        self.title_label.config(text="Auto-Start Configuration")

        ttk.Label(
            self.content_frame,
            text="Configure GoXLR Discord Sync to start automatically with Windows",
            font=("Arial", 11)
        ).pack(pady=20)

        self.autostart_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self.content_frame,
            text="Start with Windows",
            variable=self.autostart_var
        ).pack(pady=10)

        ttk.Label(
            self.content_frame,
            text="This will create a shortcut in the Windows Startup folder.",
            font=("Arial", 9),
            foreground="gray"
        ).pack()

        ttk.Button(
            self.content_frame,
            text="Apply Auto-Start Settings",
            command=self.setup_autostart
        ).pack(pady=30)

        self.autostart_status = ttk.Label(
            self.content_frame,
            text="",
            font=("Arial", 10)
        )
        self.autostart_status.pack()

    def setup_autostart(self):
        startup_folder = os.path.join(
            os.getenv('APPDATA'),
            'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup'
        )
        vbs_path = os.path.join(startup_folder, 'GoXLR_Discord_Sync.vbs')

        try:
            if self.autostart_var.get():
                # Check if exe exists
                exe_path = os.path.join(self.install_dir, "GoXLR_Discord_Sync.exe")
                if os.path.exists(exe_path):
                    script_path = exe_path
                else:
                    script_path = os.path.join(self.install_dir, "goxlr_discord_sync.pyw")

                # Create VBS launcher
                with open(vbs_path, 'w') as f:
                    f.write('Set WshShell = CreateObject("WScript.Shell")\n')
                    if exe_path == script_path:
                        f.write(f'WshShell.Run """{script_path}""", 0, False\n')
                    else:
                        f.write(f'WshShell.Run "pythonw ""{script_path}""", 0, False\n')

                self.autostart_status.config(
                    text="✓ Auto-start enabled!",
                    foreground="green"
                )
            else:
                # Remove auto-start
                if os.path.exists(vbs_path):
                    os.remove(vbs_path)

                self.autostart_status.config(
                    text="✓ Auto-start disabled!",
                    foreground="orange"
                )

            messagebox.showinfo("Success", "Auto-start configuration applied!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to configure auto-start: {e}")

    # Step 6: Complete
    def step_complete(self):
        self.title_label.config(text="Setup Complete!")

        ttk.Label(
            self.content_frame,
            text="✓ GoXLR Discord Sync is now installed and configured!",
            font=("Arial", 12, "bold"),
            foreground="green"
        ).pack(pady=30)

        info = """
Next steps:
1. Make sure GoXLR Utility is running
2. Make sure Discord is running
3. Click "Launch Now" to start GoXLR Discord Sync

The program will:
• Run in the background with a system tray icon
• Connect to Discord (you'll need to authorize on first run)
• Sync your Cough button with Discord mute

You can quit the application by right-clicking the tray icon and selecting "Quit".
        """

        ttk.Label(
            self.content_frame,
            text=info,
            justify=tk.LEFT,
            font=("Arial", 10)
        ).pack(pady=20)

        # Launch button
        ttk.Button(
            self.content_frame,
            text="🚀 Launch Now",
            command=self.launch_app
        ).pack(pady=20)

        self.next_btn.config(text="Close")

    def launch_app(self):
        try:
            exe_path = os.path.join(SCRIPT_DIR, "GoXLR_Discord_Sync.exe")
            if os.path.exists(exe_path):
                subprocess.Popen([exe_path], cwd=SCRIPT_DIR)
            else:
                subprocess.Popen(
                    [sys.executable, "goxlr_discord_sync.pyw"],
                    cwd=SCRIPT_DIR
                )

            messagebox.showinfo(
                "Launched",
                "GoXLR Discord Sync is now running!\n\nCheck your system tray for the icon."
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch: {e}")

    def uninstall_app(self):
        """Uninstall GoXLR Discord Sync"""
        result = messagebox.askyesno(
            "Confirm Uninstall",
            "Are you sure you want to uninstall GoXLR Discord Sync?\n\n"
            "This will:\n"
            "• Stop the running application\n"
            "• Remove auto-start\n"
            "• Delete the executable\n"
            "• Keep configuration files (client_id.txt, etc.)"
        )

        if not result:
            return

        import subprocess
        import time
        uninstall_log = []

        try:
            # Stop running process (ignore errors if not running)
            uninstall_log.append("Stopping GoXLR_Discord_Sync if running...")
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/IM", "GoXLR_Discord_Sync.exe"],
                    capture_output=True,
                    timeout=10
                )
                if result.returncode == 0:
                    uninstall_log.append("✓ Stopped running application")
                    time.sleep(2)
                else:
                    uninstall_log.append("  Application was not running")
            except subprocess.TimeoutExpired:
                uninstall_log.append("⚠ Timeout stopping app (continuing anyway)")
            except Exception as e:
                uninstall_log.append(f"  Could not stop app: {e}")

            # Remove auto-start and extract installation path from VBS
            install_path = None
            try:
                startup_folder = os.path.join(
                    os.getenv('APPDATA'),
                    'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup'
                )
                vbs_path = os.path.join(startup_folder, 'GoXLR_Discord_Sync.vbs')

                # Try to extract installation path from VBS before deleting
                if os.path.exists(vbs_path):
                    try:
                        with open(vbs_path, 'r') as f:
                            content = f.read()
                            # Extract path from: WshShell.Run """C:\path\to\GoXLR_Discord_Sync.exe"""
                            import re
                            match = re.search(r'WshShell\.Run\s+"""([^"]+)"""', content)
                            if match:
                                exe_path = match.group(1)
                                install_path = os.path.dirname(exe_path)
                    except:
                        pass

                    os.remove(vbs_path)
                    uninstall_log.append("✓ Removed auto-start")
                else:
                    uninstall_log.append("  Auto-start not found")
            except Exception as e:
                uninstall_log.append(f"⚠ Could not remove auto-start: {e}")

            # Delete executable (use extracted path or fallback to SCRIPT_DIR)
            if install_path is None:
                install_path = SCRIPT_DIR

            try:
                exe_path = os.path.join(install_path, "GoXLR_Discord_Sync.exe")
                if os.path.exists(exe_path):
                    os.remove(exe_path)
                    uninstall_log.append("✓ Removed executable")
                else:
                    uninstall_log.append("  Executable not found")
            except Exception as e:
                uninstall_log.append(f"⚠ Could not remove executable: {e}")

            # Delete requirements.txt if extracted
            try:
                req_path = os.path.join(install_path, "requirements.txt")
                if os.path.exists(req_path):
                    os.remove(req_path)
                    uninstall_log.append("✓ Removed requirements.txt")
            except Exception as e:
                uninstall_log.append(f"⚠ Could not remove requirements.txt: {e}")

            messagebox.showinfo(
                "Uninstall Complete",
                "GoXLR Discord Sync has been uninstalled!\n\n" +
                "\n".join(uninstall_log) +
                "\n\nConfiguration files have been kept.\n"
                "The setup will now close."
            )

            # Close the setup after uninstall
            self.root.quit()

        except Exception as e:
            messagebox.showerror(
                "Uninstall Error",
                f"An unexpected error occurred:\n{e}\n\n" +
                "Partial uninstall log:\n" +
                "\n".join(uninstall_log)
            )

def main():
    root = tk.Tk()
    app = SetupWizard(root)
    root.mainloop()

if __name__ == "__main__":
    main()
