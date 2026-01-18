# GoXLR Discord Sync

Synchronise le bouton Cough de ta GoXLR avec le mute de Discord via l'API RPC.

Quand tu appuies sur le bouton Cough de ta GoXLR, Discord se mute/démute automatiquement !

## Prérequis

- **Windows**
- **[GoXLR Utility](https://github.com/GoXLR-on-Linux/goxlr-utility)** installé et lancé
- **Discord** (application Windows) lancé
- **Python 3.x** avec pip

## Installation

1. Clone ou télécharge ce repo dans un dossier permanent :
   ```
   git clone https://github.com/ton-username/goxlr-discord-sync.git
   cd goxlr-discord-sync
   ```

2. Lance `install.bat` pour installer les dépendances et configurer le démarrage automatique

   Ou installe manuellement :
   ```
   pip install -r requirements.txt
   ```

3. Lance le script une première fois :
   ```
   python goxlr_discord_sync.pyw
   ```

## Première configuration

Le script te guidera pour créer une application Discord :

1. Va sur https://discord.com/developers/applications
2. Clique sur **"New Application"** et donne-lui un nom
3. Copie le **Application ID** (Client ID)
4. Va dans **OAuth2** et copie le **Client Secret**
5. Dans **OAuth2 > Redirects**, ajoute : `http://127.0.0.1:9543/callback`
6. Sauvegarde

Le script te demandera ensuite de coller le Client ID et le Client Secret.

## Fichiers créés

- `client_id.txt` : Ton Client ID Discord
- `client_secret.txt` : Ton Client Secret Discord (ne pas partager !)
- `discord_token.json` : Token d'accès Discord (renouvelé automatiquement)

## Utilisation

Une fois configuré, le script :
- Se lance automatiquement au démarrage de Windows
- Écoute le bouton **Cough** de ta GoXLR
- Mute/démute Discord quand tu appuies dessus

## Dépannage

**Le script ne se connecte pas à GoXLR ?**
- Vérifie que GoXLR Utility est lancé

**Le script ne se connecte pas à Discord ?**
- Vérifie que Discord est lancé
- Supprime `discord_token.json` et relance pour ré-autoriser

**Erreur d'authentification ?**
- Vérifie que le Redirect URI est bien configuré dans Discord
- Supprime `client_id.txt`, `client_secret.txt` et `discord_token.json` puis relance

## Désinstallation

Supprime le fichier dans le dossier Startup :
```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GoXLR_Discord_Sync.vbs
```
