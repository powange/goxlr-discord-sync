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
- 🔐 Client secret and tokens encrypted with Windows DPAPI (owner-only file on Linux)
- 🔔 Optional notifications when a connection is lost or restored, and when an update is available
- 🚀 Starts with Windows (or on login under Linux)
- 🌍 English and French interface, following the system language (or set in Settings › Behaviour)
- 🪶 Native application (Rust + Tauri): 3 MB installer

## 📥 Installation

**Windows 10/11**

1. Download `GoXLR_Setup.exe` from [Releases](https://github.com/powange/goxlr-discord-sync/releases)
   (optional: check it against `SHA256SUMS.txt` with `certutil -hashfile GoXLR_Setup.exe SHA256`)
2. Run it and follow the wizard
3. Enter your Discord application in the settings window that opens (see below), then click **Authorize** in the window that Discord shows

**Linux (x86_64)**: install `goxlr-discord-sync_amd64.deb` (Debian, Ubuntu…) or run `GoXLR_Discord_Sync_amd64.AppImage`. The tray icon needs a desktop with a system tray (AppIndicator extension on GNOME).

**Updating**: when a new version is published, a notification tells you. Choose **Install update** in the tray menu and confirm: the app downloads the new version, checks its signature, installs it and starts again. Nothing is installed without your confirmation, and your settings are kept.

**Coming from version 2.x** (Python): update from the tray menu as usual, or run the new `GoXLR_Setup.exe`. Version 3 keeps your settings, Discord authorization and autostart, and removes the old version on its first start.

### Requirements

- [GoXLR Utility](https://github.com/GoXLR-on-Linux/goxlr-utility) running
- Discord desktop app running
- Windows: WebView2 (built into Windows 10/11, installed by the setup if missing)

## 🔧 Discord application

Discord only lets an application control your voice settings if you created it yourself. The settings window walks you through it:

1. Go to https://discord.com/developers/applications and click **New Application**
2. Copy the **Application ID** (Client ID)
3. In **OAuth2**, copy the **Client Secret**
4. In **OAuth2 › Redirects**, add `http://127.0.0.1:9543/callback` and save
5. Paste both values in **Settings › Discord**, click **Check**, then **Save**

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
| `%APPDATA%\GoXLR Discord Sync\logs\` | Log (rotated, 3 × 1 MB) |

On Linux the folder is `~/.config/goxlr-discord-sync/`. The application itself is installed in `%LOCALAPPDATA%\GoXLR Discord Sync`.

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

Open Windows **Settings › Apps › Installed apps**, find **GoXLR Discord Sync** and choose **Uninstall**. It removes the application and the autostart; tick *Delete the application data* to also delete your settings and Discord credentials.

Linux: remove the `goxlr-discord-sync` package (or the AppImage), and `~/.config/goxlr-discord-sync/` for the settings.

## 🛠️ Development

Requirements: [Rust](https://rustup.rs) (stable) and Node.js (for the Tauri CLI). On Linux, also the WebKitGTK and AppIndicator development packages: `sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev`.

```bash
git clone https://github.com/powange/goxlr-discord-sync.git
cd goxlr-discord-sync
cargo run -p goxlr-discord-sync                 # run the app (debug build)
cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace                          # tests (fake GoXLR Utility, fake Discord)
npx @tauri-apps/cli@2 build                     # installers in target/release/bundle/
```

Set `GOXLR_DISCORD_SYNC_HOME` to a folder to run with separate settings, without touching your real ones.

**Release**: bump `version` in the root `Cargo.toml`, then push a matching tag (`v3.0.0`). CI checks the version, runs the tests, builds the Windows setup and the Linux packages, signs them for the updater and publishes the release with `SHA256SUMS.txt` and `latest.json`.

```
crates/core/            # gds-core: everything but the user interface
├── src/engine.rs       # GoXLR ↔ Discord synchronisation (reconnection, authorization)
├── src/sync.rs         # pure decision rules (mute functions, deafen/mute ordering)
├── src/goxlr.rs        # GoXLR Utility websocket client, JSON patch model
├── src/discord.rs      # Discord RPC client (named pipe / Unix socket)
├── src/auth.rs         # OAuth2 tokens, browser fallback
├── src/config.rs, secrets.rs, i18n.rs
├── i18n/fr.json        # French translations
└── tests/              # engine scenarios, translation coverage
src-tauri/              # the application: tray, settings commands, updates, autostart
ui/                     # settings window (HTML, CSS, JavaScript)
```

## 🔏 Code signing

The executables are not signed yet, so Windows SmartScreen may warn on the first download. The release workflow (`.github/workflows/rust.yml`) signs them with SignPath as soon as it is configured; until then releases are built unsigned.

1. Apply at https://signpath.org (SignPath Foundation, open-source program) with this repository.
2. In SignPath, create the project, paste `signing/signpath-artifact-configuration.xml` as its artifact configuration, and add GitHub as trusted build system for this repository.
3. In GitHub, **Settings › Secrets and variables › Actions**:
   - secret `SIGNPATH_API_TOKEN`;
   - variables `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG`, `SIGNPATH_POLICY_SLUG`.

The application is signed before the setup bundles it, the setup is signed next, the workflow checks both signatures, and the updater signature and `SHA256SUMS.txt` are computed from the signed files.

**Updater signature**: updates are only installed when they are signed with the key whose public part is in `src-tauri/tauri.conf.json` (`plugins.updater.pubkey`). The private key and its password are the secrets `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. Keep a backup of both: without them, installed versions can no longer update themselves.

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

Signed releases start with the first version published after the project is accepted by the SignPath Foundation; earlier releases are unsigned.

- Committers and reviewers: [powange](https://github.com/powange) ([contributors](https://github.com/powange/goxlr-discord-sync/graphs/contributors))
- Approvers: [powange](https://github.com/powange) (repository owner)

Only executables built by the GitHub Actions workflow of this repository from tagged sources are signed, and every signing request is approved by hand. Pull requests from other contributors are reviewed before they are merged.

## Privacy policy

GoXLR Discord Sync does not collect personal data and has no telemetry or analytics.

It only connects to:

| Where | When | What is sent |
|---|---|---|
| GoXLR Utility on this computer (`localhost`) | always | GoXLR commands (two-way sync, button actions) |
| The Discord client on this computer (local RPC) | always | voice settings changes (mute, deafen, leave the channel) |
| `discord.com` | when you authorize the application | your application's Client ID and secret, the authorization code and tokens |
| `github.com` | once a day if update checks are enabled, and when you confirm an update | a standard request for the latest release, then the download |

The Discord Client ID, Client Secret and access tokens are stored only on your computer, in `%APPDATA%\GoXLR Discord Sync` (secrets encrypted with Windows DPAPI; on Linux in `~/.config/goxlr-discord-sync`, readable only by you). Nothing is sent to the author of this project. Update checks can be turned off in **Settings › Behaviour**. Discord's [privacy policy](https://discord.com/privacy) and GitHub's [privacy statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement) apply to the connections to their services.

## 📄 License

MIT, see [LICENSE](LICENSE).

## 🙏 Acknowledgments

- [GoXLR Utility](https://github.com/GoXLR-on-Linux/goxlr-utility) for its WebSocket API
