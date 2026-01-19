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

# === Configuration files ===
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
    global discord_client_id, client_secret
    
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
    
    # First time setup
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
    global discord_rpc
    
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
        print("✓ Connected to Discord!")
        return True
    except Exception as e:
        print(f"Discord connection error: {e}")
        
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
                print("✓ Connected to Discord!")
                return True
            except Exception as e2:
                print(f"Error: {e2}")
                return False
        
        return False

async def sync_mute_state(goxlr_muted):
    """Sync state with Discord"""
    global is_muted, discord_rpc
    
    try:
        is_muted = goxlr_muted
        await discord_rpc.set_voice_settings(mute=is_muted)
        status = "🔇 Muted" if is_muted else "🔊 Unmuted"
        print(f"  → Discord: {status}")
        return True
        
    except Exception as e:
        print(f"  → Discord error: {e}")
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
    global discord_rpc
    
    last_cough_state = None
    discord_connected = False
    
    while True:
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
        
        try:
            async with websockets.connect(GOXLR_WEBSOCKET_URL) as ws:
                print("✓ Connected to GoXLR Utility")
                
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
                                
                                # Sync initial state
                                if last_cough_state and last_cough_state != "Unmuted":
                                    success = await sync_mute_state(True)
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
                                    print(f"Cough: {last_cough_state} → {new_state}")
                                    
                                    # Sync with Discord
                                    goxlr_muted = (new_state != "Unmuted")
                                    success = await sync_mute_state(goxlr_muted)
                                    
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
    print("=" * 50)
    print("   GoXLR Discord Sync")
    print("=" * 50)
    print()
    
    # Initial setup
    if not first_time_setup():
        sys.exit(1)
    
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\nScript stopped.")

if __name__ == "__main__":
    main()
