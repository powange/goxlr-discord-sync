"""
GoXLR Cough Button → Discord Mute Sync
Uses Discord RPC API with local OAuth2 (async version)
Auto-reconnects if Discord or GoXLR restarts
"""

import asyncio
import json
import sys
import os
import webbrowser
import urllib.parse
import http.server

# Fix for PyInstaller --windowed mode
# Only redirect if truly None, don't open devnull which can cause slowdowns
if sys.stdin is None:
    import io
    sys.stdin = io.StringIO()
if sys.stdout is None:
    import io
    sys.stdout = io.StringIO()
if sys.stderr is None:
    import io
    sys.stderr = io.StringIO()

# === Configuration files ===
# Detect if running as compiled exe or script
if getattr(sys, 'frozen', False):
    # Running as compiled exe - use exe directory
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    # Running as script
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CLIENT_ID_FILE = os.path.join(SCRIPT_DIR, "client_id.txt")
SECRET_FILE = os.path.join(SCRIPT_DIR, "client_secret.txt")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "discord_token.json")

# === GoXLR Configuration ===
GOXLR_WEBSOCKET_URL = "ws://localhost:14564/api/websocket"
REDIRECT_PORT = 9543

# === Reconnection delays ===
DISCORD_RETRY_DELAY = 10  # seconds
GOXLR_RETRY_DELAY = 5     # seconds

# === Global variables ===
discord_client_id = None
client_secret = None
discord_rpc = None
is_muted = False
tray_icon = None
app_running = True
status_text = "Initializing..."
cached_icons = {}  # Cache for icon images

# === Imports ===
try:
    from pypresence import AioClient as DiscordClient
except ImportError:
    print("ERROR: Module 'pypresence' missing.")
    print("Install it with: pip install pypresence")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: Module 'requests' missing.")
    print("Install it with: pip install requests")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("ERROR: Module 'websockets' missing.")
    print("Install it with: pip install websockets")
    sys.exit(1)

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("ERROR: Modules 'pystray' or 'Pillow' missing.")
    print("Install them with: pip install pystray Pillow")
    sys.exit(1)

import threading

# === System Tray Functions ===

def create_icon_image(color):
    """Create a simple colored circle icon (cached)"""
    global cached_icons

    # Return cached version if exists
    if color in cached_icons:
        return cached_icons[color]

    size = 64
    image = Image.new('RGB', (size, size), color='black')
    draw = ImageDraw.Draw(image)

    # Draw circle
    margin = 8
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
        outline='white',
        width=2
    )

    # Cache the image
    cached_icons[color] = image
    return image

def update_tray_icon():
    """Update tray icon based on current state (thread-safe)"""
    global tray_icon, is_muted

    if tray_icon:
        def _update():
            try:
                # Green = unmuted, Red = muted
                color = 'red' if is_muted else 'green'
                tray_icon.icon = create_icon_image(color)

                # Update title (tooltip)
                status = "Muted" if is_muted else "Unmuted"
                tray_icon.title = f"GoXLR Discord Sync - {status}"
            except Exception as e:
                print(f"Error updating tray icon: {e}")

        # Run in tray icon's thread
        try:
            _update()
        except:
            pass

def on_quit(icon, item):
    """Quit the application"""
    global app_running
    print("\nShutting down...")
    app_running = False
    icon.stop()
    # Force exit
    os._exit(0)

def on_show_status(icon, item):
    """Show current status notification"""
    global status_text, is_muted, tray_icon
    status = "🔇 Muted" if is_muted else "🔊 Unmuted"

    if tray_icon:
        tray_icon.notify(
            title="GoXLR Discord Sync",
            message=f"{status}\n{status_text}"
        )

def setup_tray_icon():
    """Setup system tray icon"""
    global tray_icon

    # Create menu
    menu = pystray.Menu(
        pystray.MenuItem("Status", on_show_status),
        pystray.MenuItem("Quit", on_quit)
    )

    # Create icon
    icon_image = create_icon_image('orange')  # Orange = starting
    tray_icon = pystray.Icon(
        "goxlr_sync",
        icon_image,
        "GoXLR Discord Sync - Starting...",
        menu
    )

    # Run in thread
    threading.Thread(target=tray_icon.run, daemon=True).start()

def save_token(token_data):
    """Save token for future sessions"""
    with open(TOKEN_FILE, 'w') as f:
        json.dump(token_data, f)

def load_token():
    """Load saved token"""
    try:
        with open(TOKEN_FILE, 'r') as f:
            return json.load(f)
    except:
        return None

def get_redirect_uri():
    return f"http://127.0.0.1:{REDIRECT_PORT}/callback"

def exchange_code_for_token(code):
    """Exchange authorization code for access token"""
    data = {
        'client_id': discord_client_id,
        'client_secret': client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': get_redirect_uri()
    }
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    response = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Token exchange error: {response.status_code} - {response.text}")
        return None

def refresh_access_token(refresh_tok):
    """Refresh an expired access token"""
    data = {
        'client_id': discord_client_id,
        'client_secret': client_secret,
        'grant_type': 'refresh_token',
        'refresh_token': refresh_tok
    }
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    response = requests.post('https://discord.com/api/oauth2/token', data=data, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        return None

class OAuthHandler(http.server.BaseHTTPRequestHandler):
    """Handler to receive OAuth callback"""
    auth_code = None
    
    def do_GET(self):
        if self.path.startswith('/callback'):
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            
            if 'code' in params:
                OAuthHandler.auth_code = params['code'][0]
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"""
                <html><body style="font-family: Arial; text-align: center; padding-top: 50px;">
                <h1>Authorization successful!</h1>
                <p>You can close this window and return to the script.</p>
                </body></html>
                """)
            else:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Silence logs

def get_authorization_code():
    """Open browser for authorization and get the code"""
    
    # Start a temporary local HTTP server
    server = http.server.HTTPServer(('127.0.0.1', REDIRECT_PORT), OAuthHandler)
    server.timeout = 120  # 2 minutes timeout
    
    # Build authorization URL
    scopes = "identify rpc rpc.voice.read rpc.voice.write"
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={discord_client_id}"
        f"&redirect_uri={urllib.parse.quote(get_redirect_uri())}"
        f"&response_type=code"
        f"&scope={urllib.parse.quote(scopes)}"
    )
    
    print("Opening browser for Discord authorization...")
    webbrowser.open(auth_url)
    
    print("Waiting for authorization...")
    
    # Wait for callback
    OAuthHandler.auth_code = None
    while OAuthHandler.auth_code is None:
        server.handle_request()
    
    server.server_close()
    return OAuthHandler.auth_code

def first_time_setup():
    """Initial setup - ask for Client ID and Secret"""
    global discord_client_id, client_secret, tray_icon

    need_setup = False

    # Check if Client ID exists
    if os.path.exists(CLIENT_ID_FILE):
        with open(CLIENT_ID_FILE, 'r') as f:
            discord_client_id = f.read().strip()
    else:
        need_setup = True

    # Check if Client Secret exists
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, 'r') as f:
            client_secret = f.read().strip()
    else:
        need_setup = True

    if not need_setup:
        return True

    # Check if running without console (compiled exe or pythonw)
    # Use frozen as reliable indicator
    if getattr(sys, 'frozen', False):
        # Running as compiled exe - can't use input()
        error_msg = (
            "First time setup required!\n\n"
            "Please run: python setup_gui.py\n\n"
            "Or create these files manually:\n"
            "- client_id.txt\n"
            "- client_secret.txt"
        )

        if tray_icon:
            tray_icon.notify(
                title="GoXLR Discord Sync - Setup Required",
                message=error_msg
            )

        # Don't auto-launch setup - user should run it manually
        # to avoid infinite loops

        return False

    # First time setup (console mode)
    print("=" * 50)
    print("FIRST TIME SETUP")
    print("=" * 50)
    print()
    print("You need to create a Discord application:")
    print()
    print("1. Go to https://discord.com/developers/applications")
    print("2. Click 'New Application' and give it a name")
    print("3. Copy the 'Application ID' (Client ID)")
    print("4. Go to 'OAuth2' and copy the 'Client Secret'")
    print("5. In 'OAuth2 > Redirects', add:")
    print(f"   {get_redirect_uri()}")
    print("6. Save the changes")
    print()
    print("=" * 50)
    print()

    # Ask for Client ID
    if not discord_client_id:
        discord_client_id = input("Paste the Client ID here: ").strip()
        if not discord_client_id:
            print("Client ID required!")
            return False
        with open(CLIENT_ID_FILE, 'w') as f:
            f.write(discord_client_id)
        print("Client ID saved.")

    # Ask for Client Secret
    if not client_secret:
        client_secret = input("Paste the Client Secret here: ").strip()
        if not client_secret:
            print("Client Secret required!")
            return False
        with open(SECRET_FILE, 'w') as f:
            f.write(client_secret)
        print("Client Secret saved.")

    print()
    return True

def get_access_token():
    """Get a valid access token"""
    
    # Try to reuse existing token
    token_data = load_token()
    if token_data and 'access_token' in token_data:
        # Try to refresh
        if 'refresh_token' in token_data:
            new_token_data = refresh_access_token(token_data['refresh_token'])
            if new_token_data:
                save_token(new_token_data)
                return new_token_data['access_token']
    
    # Otherwise, new authorization
    print()
    code = get_authorization_code()
    
    if not code:
        print("No authorization code received.")
        return None
    
    print("Exchanging code for token...")
    token_data = exchange_code_for_token(code)
    
    if not token_data or 'access_token' not in token_data:
        print("Unable to get token.")
        return None
    
    save_token(token_data)
    return token_data['access_token']

async def connect_discord():
    """Connect to Discord RPC with error handling"""
    global discord_rpc, status_text

    # Close old connection if exists
    if discord_rpc:
        try:
            discord_rpc.close()
        except:
            pass
        discord_rpc = None

    access_token = get_access_token()
    if not access_token:
        return False

    try:
        discord_rpc = DiscordClient(discord_client_id)
        await discord_rpc.start()
        await discord_rpc.authenticate(access_token)
        print("Connected to Discord!")
        status_text = "Connected to Discord"
        return True
    except Exception as e:
        print(f"Discord connection error: {e}")
        # Convert error to string safely, avoiding unicode issues
        error_msg = str(e).encode('ascii', errors='ignore').decode('ascii')
        status_text = f"Discord error: {error_msg}"

        # If auth error, try with new token
        if "access token" in str(e).lower() or "authenticate" in str(e).lower():
            print("Trying with new token...")
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)

            access_token = get_access_token()
            if not access_token:
                return False

            try:
                discord_rpc = DiscordClient(discord_client_id)
                await discord_rpc.start()
                await discord_rpc.authenticate(access_token)
                print("Connected to Discord!")
                status_text = "Connected to Discord"
                return True
            except Exception as e2:
                print(f"Error: {e2}")
                error_msg2 = str(e2).encode('ascii', errors='ignore').decode('ascii')
                status_text = f"Discord error: {error_msg2}"
                return False

        return False

async def sync_mute_state(goxlr_muted):
    """Sync state with Discord"""
    global is_muted, discord_rpc, status_text
    import time

    try:
        start_time = time.time()
        is_muted = goxlr_muted

        # Update icon BEFORE Discord call for immediate feedback
        update_tray_icon()

        await discord_rpc.set_voice_settings(mute=is_muted)

        elapsed = time.time() - start_time
        status = "Muted" if is_muted else "Unmuted"
        print(f"  → Discord: {status} (took {elapsed:.2f}s)")
        status_text = f"Synced - {status}"

        return True

    except Exception as e:
        print(f"  → Discord error: {e}")
        status_text = f"Sync error: {e}"
        return False

async def wait_for_goxlr():
    """Wait for GoXLR Utility to be available"""
    print("Waiting for GoXLR Utility...")
    
    while True:
        try:
            async with websockets.connect(GOXLR_WEBSOCKET_URL) as ws:
                # Connection test successful
                return True
        except Exception:
            pass
        
        await asyncio.sleep(GOXLR_RETRY_DELAY)

async def wait_for_discord():
    """Wait for Discord to be available and connect"""
    print("Waiting for Discord...")
    
    while True:
        if await connect_discord():
            return True
        
        print(f"Discord not available. Retrying in {DISCORD_RETRY_DELAY}s...")
        await asyncio.sleep(DISCORD_RETRY_DELAY)

async def main_loop():
    """Main loop with auto-reconnect"""
    global discord_rpc, app_running, status_text

    last_cough_state = None
    discord_connected = False

    while app_running:
        # Wait for Discord if not connected
        if not discord_connected:
            print()
            print("Connecting to Discord RPC...")
            discord_connected = await connect_discord()
            
            if not discord_connected:
                print(f"Discord not available. Retrying in {DISCORD_RETRY_DELAY}s...")
                await asyncio.sleep(DISCORD_RETRY_DELAY)
                continue
        
        # Connect to GoXLR
        print()
        print("Connecting to GoXLR Utility...")
        status_text = "Connecting to GoXLR..."

        try:
            async with websockets.connect(GOXLR_WEBSOCKET_URL) as ws:
                print("Connected to GoXLR Utility")
                status_text = "Connected to GoXLR & Discord"
                
                # Get initial state
                request = {"id": 1, "data": "GetStatus"}
                await ws.send(json.dumps(request))
                
                response = await ws.recv()
                result = json.loads(response)
                
                # Find initial Cough state
                if "data" in result and "Status" in result["data"]:
                    status = result["data"]["Status"]
                    if "mixers" in status:
                        for serial, mixer in status["mixers"].items():
                            if "cough_button" in mixer:
                                last_cough_state = mixer["cough_button"].get("state")
                                print(f"Initial Cough state: {last_cough_state}")

                                # Sync initial state (default to unmuted if unknown)
                                if last_cough_state:
                                    goxlr_muted = (last_cough_state != "Unmuted")
                                else:
                                    goxlr_muted = False  # Default to unmuted

                                success = await sync_mute_state(goxlr_muted)
                                if not success:
                                    discord_connected = False
                                break
                
                print()
                print("=" * 50)
                print("  LISTENING - Press Cough to mute Discord")
                print("=" * 50)
                print()
                
                # Listen for real-time patches
                while True:
                    message = await ws.recv()
                    data = json.loads(message)
                    
                    if "data" in data and "Patch" in data["data"]:
                        patches = data["data"]["Patch"]
                        for patch in patches:
                            path = patch.get("path", "")

                            if "cough_button/state" in path:
                                new_state = patch.get("value")
                                if new_state is not None and new_state != last_cough_state:
                                    import time
                                    event_time = time.time()
                                    print(f"Cough: {last_cough_state} → {new_state}")

                                    # Sync with Discord
                                    goxlr_muted = (new_state != "Unmuted")

                                    # Start sync immediately (non-blocking for UI feedback)
                                    success = await sync_mute_state(goxlr_muted)

                                    total_time = time.time() - event_time
                                    print(f"  Total time from event: {total_time:.2f}s")

                                    if not success:
                                        # Discord disconnected, reconnect
                                        discord_connected = False
                                        print("Discord disconnected. Reconnecting...")

                                        # Close properly
                                        try:
                                            discord_rpc.close()
                                        except:
                                            pass

                                        # Wait and reconnect
                                        await asyncio.sleep(2)
                                        discord_connected = await connect_discord()

                                        if discord_connected:
                                            # Resync state
                                            await sync_mute_state(goxlr_muted)

                                    last_cough_state = new_state
                                    
        except asyncio.CancelledError:
            print("Shutdown requested.")
            break
        except Exception as e:
            error_msg = str(e)
            
            # Differentiate error types
            if "ConnectionRefusedError" in error_msg or "Connect call failed" in error_msg or "connection" in error_msg.lower():
                print(f"GoXLR Utility not available. Retrying in {GOXLR_RETRY_DELAY}s...")
            else:
                print(f"GoXLR connection lost: {e}")
                print(f"Reconnecting in {GOXLR_RETRY_DELAY}s...")
            
            await asyncio.sleep(GOXLR_RETRY_DELAY)

def main():
    global app_running

    print("=" * 50)
    print("   GoXLR Discord Sync")
    print("=" * 50)
    print()

    # Initial setup
    if not first_time_setup():
        sys.exit(1)

    # Setup system tray icon
    print("Starting system tray icon...")
    setup_tray_icon()

    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\nScript stopped.")
        app_running = False
    finally:
        # Clean up tray icon
        if tray_icon:
            tray_icon.stop()

if __name__ == "__main__":
    main()
