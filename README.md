# GoXLR Discord Sync

Synchronize your GoXLR Cough button with Discord mute via the RPC API.

When you press the Cough button on your GoXLR, Discord automatically mutes/unmutes!

![System Tray](https://img.shields.io/badge/System%20Tray-Icon-green) ![Auto%20Start](https://img.shields.io/badge/Auto%20Start-Windows-blue) ![Instant%20Sync](https://img.shields.io/badge/Sync-Instant-red)

## ✨ Features

- 🎙️ **Instant synchronization** between GoXLR Cough button and Discord mute
- 🔄 **Auto-reconnect** if Discord or GoXLR Utility restarts
- 🎨 **System tray icon** with visual status (green = unmuted, red = muted)
- 🚀 **Auto-start** with Windows
- 📦 **Easy setup** with graphical wizard

## 📥 Installation (Easy Way)

### For Users

1. **Download** the latest `GoXLR_Setup.exe` from [Releases](https://github.com/powange/goxlr-discord-sync/releases)
2. **Run** `GoXLR_Setup.exe`
3. **Follow** the setup wizard
4. **Done!** The app will start automatically with Windows

### Requirements

- **Windows** (10/11)
- **[GoXLR Utility](https://github.com/GoXLR-on-Linux/goxlr-utility)** installed and running
- **Discord** desktop app running

## 🛠️ Installation (Developer)

If you want to run from source or contribute:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/goxlr-discord-sync.git
   cd goxlr-discord-sync
   ```

2. **Run the setup wizard:**
   ```bash
   python setup_gui.py
   ```

   Or install manually:
   ```bash
   pip install -r requirements.txt
   python goxlr_discord_sync.pyw
   ```

3. **Build executables** (optional):
   ```bash
   build_all.bat
   ```
   This creates `dist\GoXLR_Setup.exe` (standalone installer)

## 🔧 First Time Setup

The setup wizard will guide you through creating a Discord application:

1. Go to https://discord.com/developers/applications
2. Click **"New Application"** and give it a name (e.g., "GoXLR Sync")
3. Copy the **Application ID** (Client ID)
4. Go to **OAuth2** tab and copy the **Client Secret**
5. In **OAuth2 > Redirects**, add: `http://127.0.0.1:9543/callback`
6. Click **Save Changes**
7. Paste the Client ID and Secret in the setup wizard

## 💡 Usage

Once installed:
- ✅ The app runs in the background (system tray)
- ✅ Look for the colored icon: 🟢 Green = Unmuted | 🔴 Red = Muted
- ✅ Press the **Cough** button on your GoXLR to toggle Discord mute
- ✅ Right-click the tray icon for options (Status, Quit)

## 🔍 Troubleshooting

| Problem | Solution |
|---------|----------|
| **Can't connect to GoXLR** | Make sure [GoXLR Utility](https://github.com/GoXLR-on-Linux/goxlr-utility) is running |
| **Can't connect to Discord** | Make sure Discord desktop app is running |
| **Icon stays orange/yellow** | Check Discord authorization or recreate Discord app credentials |
| **Slow sync** | Restart the application |
| **Authentication error** | Make sure Redirect URI is `http://127.0.0.1:9543/callback` in Discord app settings |

## 🗑️ Uninstall

**Using the uninstaller:**
```bash
uninstall.bat
```

**Manual uninstall:**
1. Right-click tray icon → Quit
2. Delete auto-start file:
   ```
   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GoXLR_Discord_Sync.vbs
   ```
3. Delete the application folder

## 📂 Project Structure

```
goxlr-discord-sync/
├── goxlr_discord_sync.pyw    # Main application
├── setup_gui.py               # Setup wizard
├── build_all.bat              # Build both executables
├── build.bat                  # Build main app
├── build_setup.bat            # Build setup
├── uninstall.bat              # Uninstaller
├── requirements.txt           # Python dependencies
└── .github/workflows/         # GitHub Actions for auto-build
```

## 🤝 Contributing

Contributions are welcome! Feel free to:
- 🐛 Report bugs
- 💡 Suggest features
- 🔧 Submit pull requests

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [GoXLR Utility](https://github.com/GoXLR-on-Linux/goxlr-utility) - For the WebSocket API
- [pypresence](https://github.com/qwertyquerty/pypresence) - For Discord RPC integration
