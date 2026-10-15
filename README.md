# GoXLR Discord Sync

Mirror your GoXLR mute controls to Discord through Discord's local RPC API.

- Press the **Cough button** → Discord **mutes** your microphone
- Mute the **Chat channel** fader → Discord **deafens** you (you no longer hear others)
- Optional **two-way sync**: mute or deafen in Discord → the GoXLR follows (LEDs included)
- Give a **GoXLR button** (empty sampler slot, effect button, Bleep…) a Discord action: toggle mute, toggle deafen, leave the voice channel

## ✨ Features

- 🎙️ Instant synchronisation, aware of the GoXLR mute functions (*Mute to Stream* does not mute you in Discord)
- 🔄 Reconnects on its own when GoXLR Utility or Discord restarts, without waiting for a button press
- 🎨 Tray icon that shows the real Discord state: 🟢 mic live · 🔴 muted / deafened · 🟠 waiting for GoXLR Utility or Discord · ⚪ action needed
- ⚙️ Settings window: device, deafen channel, which mute functions count, button actions, two-way sync, notifications
- 🔊 Current voice channel in the tooltip; update notices wait until you leave the call
- 🔐 Client secret and tokens encrypted with Windows DPAPI
- 🔔 Optional notifications when a connection is lost or restored, and when an update is available
- 🚀 Starts with Windows (or on login under Linux)

## 📥 Installation

1. Download `GoXLR_Setup.exe` from [Releases](https://github.com/powange/goxlr-discord-sync/releases)
   (optional: check it against `SHA256SUMS.txt` with `certutil -hashfile GoXLR_Setup.exe SHA256`)
2. Run it and follow the wizard
3. On first launch, click **Authorize** in the window that Discord shows

Updating: when a new version is published, a notification tells you. Choose **Install update** in the tray menu and confirm: the app downloads the new installer, checks it against `SHA256SUMS.txt`, closes, updates itself and starts again. Nothing is installed without your confirmation. You can also run a newer `GoXLR_Setup.exe` by hand; your settings are kept either way.

### Requirements

- Windows 10/11 (Linux works from source, see below)
- [GoXLR Utility](https://github.com/GoXLR-on-Linux/goxlr-utility) running
- Discord desktop app running

## 🔧 Discord application

Discord only lets an application control your voice settings if you created it yourself. The wizard walks you through it:

1. Go to https://discord.com/developers/applications and click **New Application**
2. Copy the **Application ID** (Client ID)
3. In **OAuth2**, copy the **Client Secret**
4. In **OAuth2 › Redirects**, add `http://127.0.0.1:9543/callback` and save
5. Paste both values in the wizard and click **Check**

Authorization happens inside Discord. If that fails, the app falls back to your browser (that is what the redirect URL is for).

## 💡 Usage

Right-click the tray icon:

| Menu entry | What it does |
|---|---|
| Cough button → Discord mute | Turn mute sync on or off |
| Chat mute → Discord deafen | Turn deafen sync on or off |
| Two-way sync | Mirror changes made in Discord onto the GoXLR |
| Status *(or left-click)* | Connections, current state, last sync latency |
| Settings… | Open the settings window |
| Start with Windows | Toggle the autostart |
| Authorize Discord again | Start a new authorization (e.g. after revoking it in Discord) |
| Open log | Open the log file |
| Install update vX… | Shown when a new version exists: confirm to download and install it |
| Check for updates | Check now; offers the installation when a new version exists |

### Which GoXLR mute states count?

GoXLR mute buttons have a *mute function* (All, To Stream, To Voice Chat, To Headphones, To Line Out) and a hold-to-mute state that mutes everything. By default:

- the **Cough button** mutes Discord when muted to *everything* or *Voice Chat*;
- the **Chat fader** deafens Discord when muted to *everything* or *Headphones*.

So muting Chat *to Stream* (to hide Discord from your stream) no longer deafens you. Change this in **Settings › GoXLR**.

### Button actions

In **Settings › Buttons**, give a GoXLR button one of these actions: *Toggle Discord mute*, *Toggle Discord deafen*, *Leave the voice channel*. The button keeps its normal GoXLR function, so an empty sampler slot or an unused effect button works best. The Cough button and fader mutes are not offered: they are already synced.

If a GoXLR is chosen in **Settings › GoXLR** and it is not plugged in, sync pauses (orange icon) instead of using another device.

### Files

| Location | Content |
|---|---|
| `%APPDATA%\GoXLR Discord Sync\config.json` | Settings (editable, reloaded live) |
| `%APPDATA%\GoXLR Discord Sync\secrets.dat` | Client secret and tokens (DPAPI encrypted) |
| `%APPDATA%\GoXLR Discord Sync\logs\app.log` | Log (rotated, 3 × 1 MB) |

On Linux the folder is `~/.config/goxlr-discord-sync/`. Settings from version 1.x (`client_id.txt`…) are imported automatically and the plain text files are deleted.

## 🔍 Troubleshooting

| Icon / message | Solution |
|---|---|
| 🟠 *Waiting for GoXLR Utility* | Start GoXLR Utility. If you changed its port, update it in Settings › GoXLR |
| 🟠 *Waiting for Discord* | Start the Discord desktop app (the browser version has no RPC) |
| ⚪ *Setup required* | Open Settings and enter your Discord application |
| ⚪ *Discord authorization required* | Menu › Authorize Discord again, then approve in Discord |
| Browser fallback fails | Check that the redirect URL `http://127.0.0.1:9543/callback` is saved in your Discord application |
| Chat fader does nothing | Status shows on which fader the channel is; the channel must be assigned to a fader |
| Anything else | Menu › Open log |

## 🗑️ Uninstall

Open Windows **Settings › Apps › Installed apps**, find **GoXLR Discord Sync** and choose **Uninstall** (or run `GoXLR_Setup.exe` and click **Uninstall…**). It stops the app, removes the autostart and the executable, and asks whether to delete your settings.

From source: `uninstall.bat`.

## 🛠️ Development

```bash
git clone https://github.com/powange/goxlr-discord-sync.git
cd goxlr-discord-sync
pip install -r requirements-dev.txt
python setup_gui.py            # or: python goxlr_discord_sync.pyw --settings
pythonw goxlr_discord_sync.pyw # run the app (python on Linux)
ruff check . && pytest         # lint and tests
python build.py all            # Windows: dist\GoXLR_Discord_Sync.exe, dist\GoXLR_Setup.exe, SHA256SUMS.txt
```

**Linux**: GoXLR Utility comes from GoXLR-on-Linux, and the app connects to Discord's Unix socket (native, Flatpak or Snap). Run `python setup_gui.py`, or `pip install -r requirements.txt` then `python goxlr_discord_sync.pyw --autostart on`. The tray icon needs a desktop with a system tray (AppIndicator on GNOME).

**Release**: bump `__version__` in `goxlr_discord_sync/__init__.py`, then push a matching tag (`v2.0.0`). CI runs the tests, builds on Windows and publishes the release.

```
goxlr_discord_sync/
├── app.py              # entry point, tray actions, live config reload, update check
├── engine.py           # GoXLR ↔ Discord synchronisation (reconnection, authorization)
├── sync_logic.py       # pure decision rules (mute functions, deafen/mute ordering)
├── goxlr.py            # GoXLR Utility websocket client, JSON patch model
├── discord_ipc.py      # Discord RPC client (named pipe / Unix socket)
├── auth.py             # OAuth2 tokens, browser fallback
├── config.py, secure_store.py, paths.py, migration.py
├── tray.py, icons.py, settings_window.py, ui_common.py
├── autostart.py, system.py, updates.py, logging_setup.py
goxlr_discord_sync.pyw  # launcher
setup_gui.py            # installation wizard
build.py                # PyInstaller build (used by build*.bat and CI)
tests/                  # pytest suite (fake GoXLR Utility and fake Discord)
```

## 📄 License

MIT, see [LICENSE](LICENSE).

## 🙏 Acknowledgments

- [GoXLR Utility](https://github.com/GoXLR-on-Linux/goxlr-utility) for its WebSocket API
