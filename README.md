# GoXLR Discord Sync

Sync your GoXLR's Cough button with Discord mute via the RPC API.

When you press the Cough button on your GoXLR, Discord automatically mutes/unmutes!

## Requirements

- **Windows**
- **[GoXLR Utility](https://github.com/GoXLR-on-Linux/goxlr-utility)** installed and running
- **Discord** (Windows app) running
- **Python 3.x** with pip

## Installation

1. Clone or download this repo to a permanent folder:
   ```
   git clone https://github.com/your-username/goxlr-discord-sync.git
   cd goxlr-discord-sync
   ```

2. Run `install.bat` to install dependencies and configure auto-start

   Or install manually:
   ```
   pip install -r requirements.txt
   ```

3. Run the script for the first time:
   ```
   python goxlr_discord_sync.pyw
   ```

## First Time Setup

The script will guide you to create a Discord application:

1. Go to https://discord.com/developers/applications
2. Click **"New Application"** and give it a name
3. Copy the **Application ID** (Client ID)
4. Go to **OAuth2** and copy the **Client Secret**
5. In **OAuth2 > Redirects**, add: `http://127.0.0.1:9543/callback`
6. Save

The script will then ask you to paste the Client ID and Client Secret.

## Created Files

- `client_id.txt`: Your Discord Client ID
- `client_secret.txt`: Your Discord Client Secret (do not share!)
- `discord_token.json`: Discord access token (automatically renewed)

## Usage

Once configured, the script:
- Starts automatically when Windows boots
- Listens to the **Cough** button on your GoXLR
- Mutes/unmutes Discord when you press it

## Troubleshooting

**Script can't connect to GoXLR?**
- Make sure GoXLR Utility is running

**Script can't connect to Discord?**
- Make sure Discord is running
- Delete `discord_token.json` and restart to re-authorize

**Authentication error?**
- Make sure the Redirect URI is properly configured in Discord
- Delete `client_id.txt`, `client_secret.txt` and `discord_token.json` then restart

## Uninstall

Delete the file in the Startup folder:
```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GoXLR_Discord_Sync.vbs
```
