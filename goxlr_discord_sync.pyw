"""
GoXLR Cough Button → Discord Mute Sync
Utilise l'API Discord RPC avec OAuth2 local (version async)
Reconnexion automatique si Discord ou GoXLR redémarre
"""

import asyncio
import json
import sys
import os
import webbrowser
import urllib.parse
import http.server

# === Fichiers de configuration ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_ID_FILE = os.path.join(SCRIPT_DIR, "client_id.txt")
SECRET_FILE = os.path.join(SCRIPT_DIR, "client_secret.txt")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "discord_token.json")

# === Configuration GoXLR ===
GOXLR_WEBSOCKET_URL = "ws://localhost:14564/api/websocket"
REDIRECT_PORT = 9543

# === Délais de reconnexion ===
DISCORD_RETRY_DELAY = 10  # secondes
GOXLR_RETRY_DELAY = 5     # secondes

# === Variables globales ===
discord_client_id = None
client_secret = None
discord_rpc = None
is_muted = False

# === Imports ===
try:
    from pypresence import AioClient as DiscordClient
except ImportError:
    print("ERREUR: Module 'pypresence' manquant.")
    print("Installez-le avec: pip install pypresence")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERREUR: Module 'requests' manquant.")
    print("Installez-le avec: pip install requests")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("ERREUR: Module 'websockets' manquant.")
    print("Installez-le avec: pip install websockets")
    sys.exit(1)

def save_token(token_data):
    """Sauvegarde le token pour les prochaines sessions"""
    with open(TOKEN_FILE, 'w') as f:
        json.dump(token_data, f)

def load_token():
    """Charge le token sauvegardé"""
    try:
        with open(TOKEN_FILE, 'r') as f:
            return json.load(f)
    except:
        return None

def get_redirect_uri():
    return f"http://127.0.0.1:{REDIRECT_PORT}/callback"

def exchange_code_for_token(code):
    """Échange le code d'autorisation contre un access token"""
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
        print(f"Erreur token exchange: {response.status_code} - {response.text}")
        return None

def refresh_access_token(refresh_tok):
    """Rafraîchit un access token expiré"""
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
    """Handler pour recevoir le callback OAuth"""
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
                <h1>Autorisation reussie!</h1>
                <p>Tu peux fermer cette fenetre et retourner au script.</p>
                </body></html>
                """)
            else:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Silence les logs

def get_authorization_code():
    """Ouvre le navigateur pour l'autorisation et récupère le code"""
    
    # Démarrer un serveur HTTP local temporaire
    server = http.server.HTTPServer(('127.0.0.1', REDIRECT_PORT), OAuthHandler)
    server.timeout = 120  # 2 minutes timeout
    
    # Construire l'URL d'autorisation
    scopes = "identify rpc rpc.voice.read rpc.voice.write"
    auth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={discord_client_id}"
        f"&redirect_uri={urllib.parse.quote(get_redirect_uri())}"
        f"&response_type=code"
        f"&scope={urllib.parse.quote(scopes)}"
    )
    
    print("Ouverture du navigateur pour l'autorisation Discord...")
    webbrowser.open(auth_url)
    
    print("En attente de l'autorisation...")
    
    # Attendre le callback
    OAuthHandler.auth_code = None
    while OAuthHandler.auth_code is None:
        server.handle_request()
    
    server.server_close()
    return OAuthHandler.auth_code

def first_time_setup():
    """Configuration initiale - demande Client ID et Secret"""
    global discord_client_id, client_secret
    
    need_setup = False
    
    # Vérifier si le Client ID existe
    if os.path.exists(CLIENT_ID_FILE):
        with open(CLIENT_ID_FILE, 'r') as f:
            discord_client_id = f.read().strip()
    else:
        need_setup = True
    
    # Vérifier si le Client Secret existe
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, 'r') as f:
            client_secret = f.read().strip()
    else:
        need_setup = True
    
    if not need_setup:
        return True
    
    # Première configuration
    print("=" * 50)
    print("PREMIERE CONFIGURATION")
    print("=" * 50)
    print()
    print("Tu dois creer une application Discord:")
    print()
    print("1. Va sur https://discord.com/developers/applications")
    print("2. Clique sur 'New Application' et donne-lui un nom")
    print("3. Copie le 'Application ID' (Client ID)")
    print("4. Va dans 'OAuth2' et copie le 'Client Secret'")
    print("5. Dans 'OAuth2 > Redirects', ajoute:")
    print(f"   {get_redirect_uri()}")
    print("6. Sauvegarde les changements")
    print()
    print("=" * 50)
    print()
    
    # Demander le Client ID
    if not discord_client_id:
        discord_client_id = input("Colle le Client ID ici: ").strip()
        if not discord_client_id:
            print("Client ID requis!")
            return False
        with open(CLIENT_ID_FILE, 'w') as f:
            f.write(discord_client_id)
        print("Client ID sauvegardé.")
    
    # Demander le Client Secret
    if not client_secret:
        client_secret = input("Colle le Client Secret ici: ").strip()
        if not client_secret:
            print("Client Secret requis!")
            return False
        with open(SECRET_FILE, 'w') as f:
            f.write(client_secret)
        print("Client Secret sauvegardé.")
    
    print()
    return True

def get_access_token():
    """Obtient un access token valide"""
    
    # Essayer de réutiliser un token existant
    token_data = load_token()
    if token_data and 'access_token' in token_data:
        # Vérifier si on peut rafraîchir
        if 'refresh_token' in token_data:
            new_token_data = refresh_access_token(token_data['refresh_token'])
            if new_token_data:
                save_token(new_token_data)
                return new_token_data['access_token']
    
    # Sinon, nouvelle autorisation
    print()
    code = get_authorization_code()
    
    if not code:
        print("Pas de code d'autorisation reçu.")
        return None
    
    print("Échange du code contre un token...")
    token_data = exchange_code_for_token(code)
    
    if not token_data or 'access_token' not in token_data:
        print("Impossible d'obtenir le token.")
        return None
    
    save_token(token_data)
    return token_data['access_token']

async def connect_discord():
    """Connecte à Discord RPC avec gestion des erreurs"""
    global discord_rpc
    
    # Fermer l'ancienne connexion si elle existe
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
        print("✓ Connecté à Discord!")
        return True
    except Exception as e:
        print(f"Erreur connexion Discord: {e}")
        
        # Si erreur d'auth, essayer avec un nouveau token
        if "access token" in str(e).lower() or "authenticate" in str(e).lower():
            print("Essai avec un nouveau token...")
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            
            access_token = get_access_token()
            if not access_token:
                return False
            
            try:
                discord_rpc = DiscordClient(discord_client_id)
                await discord_rpc.start()
                await discord_rpc.authenticate(access_token)
                print("✓ Connecté à Discord!")
                return True
            except Exception as e2:
                print(f"Erreur: {e2}")
                return False
        
        return False

async def sync_mute_state(goxlr_muted):
    """Synchronise l'état avec Discord"""
    global is_muted, discord_rpc
    
    try:
        is_muted = goxlr_muted
        await discord_rpc.set_voice_settings(mute=is_muted)
        status = "🔇 Muted" if is_muted else "🔊 Unmuted"
        print(f"  → Discord: {status}")
        return True
        
    except Exception as e:
        print(f"  → Erreur Discord: {e}")
        return False

async def wait_for_goxlr():
    """Attend que GoXLR Utility soit disponible"""
    print("En attente de GoXLR Utility...")
    
    while True:
        try:
            async with websockets.connect(GOXLR_WEBSOCKET_URL) as ws:
                # Test de connexion réussi
                return True
        except Exception:
            pass
        
        await asyncio.sleep(GOXLR_RETRY_DELAY)

async def wait_for_discord():
    """Attend que Discord soit disponible et connecte"""
    print("En attente de Discord...")
    
    while True:
        if await connect_discord():
            return True
        
        print(f"Discord non disponible. Nouvelle tentative dans {DISCORD_RETRY_DELAY}s...")
        await asyncio.sleep(DISCORD_RETRY_DELAY)

async def main_loop():
    """Boucle principale avec reconnexion automatique"""
    global discord_rpc
    
    last_cough_state = None
    discord_connected = False
    
    while True:
        # Attendre Discord si pas connecté
        if not discord_connected:
            print()
            print("Connexion à Discord RPC...")
            discord_connected = await connect_discord()
            
            if not discord_connected:
                print(f"Discord non disponible. Nouvelle tentative dans {DISCORD_RETRY_DELAY}s...")
                await asyncio.sleep(DISCORD_RETRY_DELAY)
                continue
        
        # Connexion à GoXLR
        print()
        print("Connexion à GoXLR Utility...")
        
        try:
            async with websockets.connect(GOXLR_WEBSOCKET_URL) as ws:
                print("✓ Connecté à GoXLR Utility")
                
                # Récupérer l'état initial
                request = {"id": 1, "data": "GetStatus"}
                await ws.send(json.dumps(request))
                
                response = await ws.recv()
                result = json.loads(response)
                
                # Trouver l'état initial du Cough
                if "data" in result and "Status" in result["data"]:
                    status = result["data"]["Status"]
                    if "mixers" in status:
                        for serial, mixer in status["mixers"].items():
                            if "cough_button" in mixer:
                                last_cough_state = mixer["cough_button"].get("state")
                                print(f"État initial du Cough: {last_cough_state}")
                                
                                # Synchroniser l'état initial
                                if last_cough_state and last_cough_state != "Unmuted":
                                    success = await sync_mute_state(True)
                                    if not success:
                                        discord_connected = False
                                break
                
                print()
                print("=" * 50)
                print("  EN ÉCOUTE - Appuie sur Cough pour muter Discord")
                print("=" * 50)
                print()
                
                # Écouter les patches en temps réel
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
                                    
                                    # Synchroniser avec Discord
                                    goxlr_muted = (new_state != "Unmuted")
                                    success = await sync_mute_state(goxlr_muted)
                                    
                                    if not success:
                                        # Discord déconnecté, on va se reconnecter
                                        discord_connected = False
                                        print("Discord déconnecté. Reconnexion...")
                                        
                                        # Fermer proprement
                                        try:
                                            discord_rpc.close()
                                        except:
                                            pass
                                        
                                        # Attendre et reconnecter
                                        await asyncio.sleep(2)
                                        discord_connected = await connect_discord()
                                        
                                        if discord_connected:
                                            # Resync l'état
                                            await sync_mute_state(goxlr_muted)
                                    
                                    last_cough_state = new_state
                                    
        except asyncio.CancelledError:
            print("Arrêt demandé.")
            break
        except Exception as e:
            error_msg = str(e)
            
            # Différencier les types d'erreurs
            if "ConnectionRefusedError" in error_msg or "Connect call failed" in error_msg or "connection" in error_msg.lower():
                print(f"GoXLR Utility non disponible. Nouvelle tentative dans {GOXLR_RETRY_DELAY}s...")
            else:
                print(f"Connexion GoXLR perdue: {e}")
                print(f"Reconnexion dans {GOXLR_RETRY_DELAY}s...")
            
            await asyncio.sleep(GOXLR_RETRY_DELAY)

def main():
    print("=" * 50)
    print("   GoXLR Discord Sync")
    print("=" * 50)
    print()
    
    # Configuration initiale
    if not first_time_setup():
        sys.exit(1)
    
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\nArrêt du script.")

if __name__ == "__main__":
    main()
