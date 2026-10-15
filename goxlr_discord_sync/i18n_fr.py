"""French catalog: English text (as written in the code) -> French text."""

FR = {
    # --- tray menu ------------------------------------------------------------
    "Cough button → Discord mute": "Bouton Cough → micro coupé dans Discord",
    "{channel} mute → Discord deafen": "Mute {channel} → sourdine dans Discord",
    "Two-way sync (Discord → GoXLR)": "Synchronisation bidirectionnelle (Discord → GoXLR)",
    "Status": "État",
    "Settings…": "Réglages…",
    "Start with Windows": "Démarrer avec Windows",
    "Start on login": "Démarrer à l'ouverture de session",
    "Authorize Discord again": "Autoriser Discord à nouveau",
    "Open log": "Ouvrir le journal",
    "Install update v{version}…": "Installer la mise à jour v{version}…",
    "Update available: v{version}": "Mise à jour disponible : v{version}",
    "Check for updates": "Rechercher des mises à jour",
    "About (v{version})": "À propos (v{version})",
    "Quit": "Quitter",

    # --- status (tooltip and Status notification) -----------------------------
    "Setup required": "Configuration requise",
    "Waiting for Discord authorization": "En attente de l'autorisation Discord",
    "Discord authorization required": "Autorisation Discord requise",
    "Waiting for Discord": "En attente de Discord",
    "Waiting for GoXLR Utility": "En attente de GoXLR Utility",
    "GoXLR {serial} not connected": "GoXLR {serial} non connecté",
    "No GoXLR connected": "Aucun GoXLR connecté",
    "Mic muted": "Micro coupé",
    "Mic live": "Micro ouvert",
    "deafened": "sourdine",
    "hearing": "son actif",
    "connected ({serial})": "connecté ({serial})",
    "connected": "connecté",
    "connected, but GoXLR {serial} is not plugged in": "connecté, mais le GoXLR {serial} n'est pas branché",
    "connected, no device": "connecté, aucun appareil",
    "not available": "indisponible",
    "GoXLR Utility: {state}": "GoXLR Utility : {state}",
    "Discord: {state}": "Discord : {state}",
    "Mic: {mic} · Audio: {audio}": "Micro : {mic} · Son : {audio}",
    "muted": "coupé",
    "live": "ouvert",
    "Voice channel: {channel}": "Salon vocal : {channel}",
    "none": "aucun",
    "fader {fader}": "fader {fader}",
    "not on a fader": "sur aucun fader",
    "{channel} channel: {where}": "Canal {channel} : {where}",
    "Last sync: {ms} ms": "Dernière synchro : {ms} ms",

    # --- notifications -----------------------------------------------------------
    "{name} reconnected": "{name} reconnecté",
    "Synchronisation is running again.": "La synchronisation a repris.",
    "{name} disconnected": "{name} déconnecté",
    "Mute synchronisation is paused until it comes back.": "La synchronisation est en pause jusqu'à son retour.",
    "Discord authorization": "Autorisation Discord",
    "Approve GoXLR Discord Sync in the Discord window.": "Autorisez GoXLR Discord Sync dans la fenêtre de Discord.",
    "Approve GoXLR Discord Sync in your browser.": "Autorisez GoXLR Discord Sync dans votre navigateur.",
    "Install update": "Installer la mise à jour",
    "Update available": "Mise à jour disponible",
    "Version {version} is available. Choose “{action}” in the tray menu.":
        "La version {version} est disponible. Choisissez « {action} » dans le menu de l'icône.",
    "Update installed": "Mise à jour installée",
    "{app} is now version {version}.": "{app} est maintenant en version {version}.",
    "Update check failed": "Échec de la recherche de mise à jour",
    "GitHub could not be reached. Try again later.": "GitHub est injoignable. Réessayez plus tard.",
    "No update available": "Aucune mise à jour",
    "You're running the latest version ({version}).": "Vous utilisez la dernière version ({version}).",
    "Autostart": "Démarrage automatique",
    "Could not change the setting: {error}": "Impossible de modifier ce réglage : {error}",
    "Downloading update": "Téléchargement de la mise à jour",
    "Downloading version {version}…": "Téléchargement de la version {version}…",

    # --- dialogs ----------------------------------------------------------------
    "{app} update": "Mise à jour de {app}",
    "{app} {version} is available (you have {current}).": "{app} {version} est disponible (vous avez la {current}).",
    "You are in a voice call: mute sync stops for a few seconds during the update.":
        "Vous êtes en appel vocal : la synchronisation s'interrompt quelques secondes pendant la mise à jour.",
    "Install it now? The application closes for a few seconds and starts again on its own.":
        "L'installer maintenant ? L'application se ferme quelques secondes puis redémarre toute seule.",
    "The update could not be installed:": "La mise à jour n'a pas pu être installée :",
    "Open the download page instead?": "Ouvrir plutôt la page de téléchargement ?",
    "Uninstall {app}?": "Désinstaller {app} ?",
    "The application stops, its autostart is removed and the executable is deleted.":
        "L'application s'arrête, son démarrage automatique est supprimé et l'exécutable est effacé.",
    "Also delete your settings and Discord credentials?": "Supprimer aussi vos réglages et vos identifiants Discord ?",
    "{app} uninstalled": "{app} désinstallé",
    "{app} is already running.": "{app} est déjà lancé.",
    "Look for its icon in the system tray.": "Cherchez son icône dans la zone de notification.",
    "{app} needs your Discord application details before it can work.":
        "{app} a besoin des informations de votre application Discord pour fonctionner.",
    "Open the settings now?": "Ouvrir les réglages maintenant ?",
    "{app} stopped because of an unexpected error:": "{app} s'est arrêté à cause d'une erreur inattendue :",
    "Details are in {path}": "Les détails sont dans {path}",

    # --- uninstall report -----------------------------------------------------------
    "Autostart removed": "Démarrage automatique supprimé",
    "Autostart: {error}": "Démarrage automatique : {error}",
    "Apps list entry: {error}": "Entrée de la liste des applications : {error}",
    "Application removed from {path}": "Application supprimée de {path}",
    "Could not delete {path}": "Impossible de supprimer {path}",
    "Application not found in {path}": "Application introuvable dans {path}",
    "Settings and credentials deleted": "Réglages et identifiants supprimés",
    "Settings kept in {path}": "Réglages conservés dans {path}",

    # --- update window ------------------------------------------------------------------
    "{app} – Update": "{app} – Mise à jour",
    "Updating {app} to version {version}…": "Mise à jour de {app} vers la version {version}…",
    "Waiting for the application to close…": "En attente de la fermeture de l'application…",
    "Stopping the application…": "Arrêt de l'application…",
    "Starting the new version…": "Démarrage de la nouvelle version…",
    "Checking that the new version starts…": "Vérification du démarrage de la nouvelle version…",
    "The new version did not start. Restoring the previous version…":
        "La nouvelle version n'a pas démarré. Restauration de la version précédente…",
    "Version {version} did not start, so the previous version was restored.":
        "La version {version} n'a pas démarré : la version précédente a été restaurée.",
    "Version {version} did not start and no previous version was kept.":
        "La version {version} n'a pas démarré et aucune version précédente n'a été conservée.",
    "This installer does not contain the application": "Ce programme d'installation ne contient pas l'application",
    "Update not installed": "Mise à jour non installée",
    "You can keep using it. Details are in {path}. Please report the problem at {url}":
        "Vous pouvez continuer à l'utiliser. Les détails sont dans {path}. Merci de signaler le problème sur {url}",
    "Update failed": "Échec de la mise à jour",
    "Run GoXLR_Setup.exe again, or download it from {url}":
        "Relancez GoXLR_Setup.exe, ou téléchargez-le depuis {url}",

    # --- settings window --------------------------------------------------------------------
    "{app} – Settings": "{app} – Réglages",
    "Buttons": "Boutons",
    "Behaviour": "Comportement",
    "Save": "Enregistrer",
    "Cancel": "Annuler",
    "Open log folder": "Ouvrir le dossier des journaux",
    "GoXLR Utility address:": "Adresse de GoXLR Utility :",
    "Device:": "Appareil :",
    "First GoXLR found": "Premier GoXLR trouvé",
    "Detect": "Détecter",
    "Channel that deafens Discord:": "Canal qui met Discord en sourdine :",
    "Cough button mutes Discord when": "Le bouton Cough coupe le micro Discord si",
    "Channel fader deafens Discord when": "Le fader du canal met Discord en sourdine si",
    "“Muted to everything” is the hold-to-mute state. The other entries match the mute function configured for "
    "the button or fader in GoXLR Utility. Example: leave “Muted to Stream” unchecked to hide a sound from your "
    "stream without muting yourself in Discord.":
        "« Coupé partout » correspond à l'appui long. Les autres choix correspondent à la fonction de mute "
        "réglée pour le bouton ou le fader dans GoXLR Utility. Exemple : laissez « Coupé pour le stream » "
        "décoché pour retirer un son du stream sans couper votre micro dans Discord.",
    "Muted to everything": "Coupé partout",
    "Muted to Stream": "Coupé pour le stream",
    "Muted to Voice Chat": "Coupé pour le chat vocal",
    "Muted to Headphones": "Coupé dans le casque",
    "Muted to Line Out": "Coupé sur la sortie ligne",
    "Give a Discord action to a GoXLR button. The button keeps its usual GoXLR function: an empty sampler slot or "
    "an unused effect button works best.":
        "Associez une action Discord à un bouton du GoXLR. Le bouton garde sa fonction habituelle : "
        "un emplacement de sampler vide ou un bouton d'effet inutilisé convient le mieux.",
    "Not assigned": "Non attribué",
    "Toggle Discord mute": "Couper/rétablir le micro Discord",
    "Toggle Discord deafen": "Activer/désactiver la sourdine Discord",
    "Leave the voice channel": "Quitter le salon vocal",
    "Bleep": "Bleep",
    "FX": "FX",
    "Megaphone": "Mégaphone",
    "Robot": "Robot",
    "Hard Tune": "Hard Tune",
    "Effect preset 1": "Préréglage d'effet 1",
    "Effect preset 2": "Préréglage d'effet 2",
    "Effect preset 3": "Préréglage d'effet 3",
    "Effect preset 4": "Préréglage d'effet 4",
    "Effect preset 5": "Préréglage d'effet 5",
    "Effect preset 6": "Préréglage d'effet 6",
    "Sampler bank A": "Banque de sampler A",
    "Sampler bank B": "Banque de sampler B",
    "Sampler bank C": "Banque de sampler C",
    "Sampler top left": "Sampler en haut à gauche",
    "Sampler top right": "Sampler en haut à droite",
    "Sampler bottom left": "Sampler en bas à gauche",
    "Sampler bottom right": "Sampler en bas à droite",
    "Sampler clear": "Effacement du sampler",
    "Each GoXLR button can only have one action.": "Chaque bouton du GoXLR ne peut avoir qu'une action.",
    "Cough button mutes Discord": "Le bouton Cough coupe le micro Discord",
    "Channel fader mute deafens Discord": "Le mute du fader du canal met Discord en sourdine",
    "Two-way sync: muting or deafening in Discord updates the GoXLR":
        "Synchronisation bidirectionnelle : couper le micro ou le son dans Discord met à jour le GoXLR",
    "Show notifications when a connection is lost or restored":
        "Afficher une notification quand une connexion est perdue ou rétablie",
    "Check for new versions once a day": "Rechercher une nouvelle version une fois par jour",
    "Language:": "Langue :",
    "Automatic": "Automatique",
    "English": "English",
    "Français": "Français",
    "Contacting GoXLR Utility…": "Connexion à GoXLR Utility…",
    "GoXLR Utility not reachable at this address ({error}).": "GoXLR Utility est injoignable à cette adresse ({error}).",
    "GoXLR Utility is running but no GoXLR is connected.": "GoXLR Utility fonctionne, mais aucun GoXLR n'est connecté.",
    "fader {fader} = {channel}": "fader {fader} = {channel}",
    "Discord application": "Application Discord",
    "The address must start with ws:// (default: {url}).": "L'adresse doit commencer par ws:// (par défaut : {url}).",
    "Settings": "Réglages",
    "Could not save the settings:": "Impossible d'enregistrer les réglages :",

    # --- Discord application form -------------------------------------------------------------
    "1. Open the Discord Developer Portal and click “New Application”.":
        "1. Ouvrez le portail développeur de Discord et cliquez sur « New Application ».",
    "2. Copy the Application ID (Client ID) from “General Information”.":
        "2. Copiez l'Application ID (Client ID) depuis « General Information ».",
    "3. In “OAuth2”, reset and copy the Client Secret.":
        "3. Dans « OAuth2 », réinitialisez puis copiez le Client Secret.",
    "4. In “OAuth2 › Redirects”, add the redirect URL below, then save.":
        "4. Dans « OAuth2 › Redirects », ajoutez l'URL de redirection ci-dessous, puis enregistrez.",
    "5. Paste the Client ID and Client Secret here and click “Check”.":
        "5. Collez ici le Client ID et le Client Secret, puis cliquez sur « Vérifier ».",
    "Open Discord Developer Portal": "Ouvrir le portail développeur Discord",
    "Redirect URL:": "URL de redirection :",
    "Copy": "Copier",
    "Copied": "Copié",
    "Show": "Afficher",
    "Check": "Vérifier",
    "Checking with Discord…": "Vérification auprès de Discord…",
    "Check failed: {error}": "Échec de la vérification : {error}",
    "Enter both the Client ID and the Client Secret.": "Saisissez le Client ID et le Client Secret.",
    "The Client ID is a number (the “Application ID” in the Developer Portal).":
        "Le Client ID est un nombre (l'« Application ID » du portail développeur).",
    "The Client ID and Client Secret are valid.": "Le Client ID et le Client Secret sont valides.",
    "Discord rejected this Client ID / Client Secret pair. Copy both values again.":
        "Discord refuse ce couple Client ID / Client Secret. Copiez à nouveau les deux valeurs.",
    "Cannot reach Discord ({error}). Check your internet connection.":
        "Discord est injoignable ({error}). Vérifiez votre connexion Internet.",
    "Discord answered HTTP {status}. Try again in a moment.": "Discord a répondu HTTP {status}. Réessayez dans un instant.",

    # --- setup wizard -------------------------------------------------------------------------
    "{app} {version} – Setup": "{app} {version} – Installation",
    "← Back": "← Précédent",
    "Next →": "Suivant →",
    "Close": "Fermer",
    "Welcome to {app}": "Bienvenue dans {app}",
    "This wizard installs and configures GoXLR Discord Sync.": "Cet assistant installe et configure GoXLR Discord Sync.",
    "What it does:": "Ce que fait l'application :",
    "Press the Cough button on your GoXLR to mute your microphone in Discord.":
        "Appuyez sur le bouton Cough du GoXLR pour couper votre micro dans Discord.",
    "Mute the Chat channel fader to deafen yourself in Discord.":
        "Coupez le fader du canal Chat pour vous mettre en sourdine dans Discord.",
    "Optionally, mute or deafen in Discord to update the GoXLR.":
        "En option, coupez le micro ou le son dans Discord pour mettre à jour le GoXLR.",
    "Runs in the system tray and reconnects on its own.":
        "Fonctionne dans la zone de notification et se reconnecte toute seule.",
    "You need:": "Il vous faut :",
    "GoXLR Utility installed and running": "GoXLR Utility installé et lancé",
    "the Discord desktop app": "l'application Discord pour ordinateur",
    "Python {version} with pip (this wizard runs on {python})": "Python {version} avec pip (cet assistant utilise {python})",
    "GoXLR Discord Sync is already installed. Continue to update or reconfigure it, or uninstall it.":
        "GoXLR Discord Sync est déjà installé. Continuez pour le mettre à jour ou le reconfigurer, ou désinstallez-le.",
    "Uninstall…": "Désinstaller…",
    "Installation folder": "Dossier d'installation",
    "Choose where to install the application. Your settings are stored separately in {path}.":
        "Choisissez où installer l'application. Vos réglages sont enregistrés à part, dans {path}.",
    "Browse…": "Parcourir…",
    "Choose a folder.": "Choisissez un dossier.",
    "Setup error": "Erreur d'installation",
    "GoXLR_Discord_Sync.exe is missing from this setup.": "GoXLR_Discord_Sync.exe est absent de ce programme d'installation.",
    "Download a fresh copy from {url}": "Téléchargez-en une nouvelle copie depuis {url}",
    "Stopping the running application and copying files…": "Arrêt de l'application en cours et copie des fichiers…",
    "Installation failed": "Échec de l'installation",
    "Could not install to {path}:": "Impossible d'installer dans {path} :",
    "Python dependencies": "Dépendances Python",
    "Install the Python modules listed in requirements.txt.": "Installez les modules Python listés dans requirements.txt.",
    "Install dependencies": "Installer les dépendances",
    "Dependencies": "Dépendances",
    "requirements.txt not found in {path}": "requirements.txt introuvable dans {path}",
    "Dependencies installed.": "Dépendances installées.",
    "Installation failed ({code}).": "Échec de l'installation ({code}).",
    "pip failed. Read the log above, fix the problem and retry.":
        "pip a échoué. Lisez le journal ci-dessus, corrigez le problème puis réessayez.",
    "Discord only lets an application control your voice settings if you created it yourself. "
    "It takes two minutes:":
        "Discord ne laisse une application contrôler vos réglages vocaux que si vous l'avez créée vous-même. "
        "Cela prend deux minutes :",
    "These values could not be confirmed with Discord.": "Discord n'a pas pu confirmer ces valeurs.",
    "Save them anyway?": "Les enregistrer quand même ?",
    "Startup, updates and privacy": "Démarrage, mises à jour et confidentialité",
    "Check for new versions once a day (contacts GitHub)": "Rechercher une nouvelle version une fois par jour (contacte GitHub)",
    "Privacy": "Confidentialité",
    "GoXLR Discord Sync collects no personal data and has no telemetry. It talks to GoXLR Utility and to Discord on this computer. Over the internet it only contacts Discord when you authorize the application, and GitHub to check for updates (if enabled above) or to download an update you confirmed. Your Discord credentials stay on this computer, encrypted.":
        "GoXLR Discord Sync ne collecte aucune donnée personnelle et n'envoie aucune télémétrie. Il communique avec GoXLR Utility et Discord sur cet ordinateur. Sur Internet, il ne contacte que Discord quand vous autorisez l'application, et GitHub pour rechercher les mises à jour (si l'option ci-dessus est cochée) ou télécharger une mise à jour que vous avez confirmée. Vos identifiants Discord restent sur cet ordinateur, chiffrés.",
    "Read the privacy policy": "Lire la politique de confidentialité",
    "Start GoXLR Discord Sync with Windows": "Démarrer GoXLR Discord Sync avec Windows",
    "Start GoXLR Discord Sync on login": "Démarrer GoXLR Discord Sync à l'ouverture de session",
    "You can change this later from the tray icon menu.": "Vous pourrez le modifier plus tard depuis le menu de l'icône.",
    "Setup complete": "Installation terminée",
    "GoXLR Discord Sync is installed.": "GoXLR Discord Sync est installé.",
    "On first launch, Discord asks you to authorize the application: click “Authorize” in the Discord window.":
        "Au premier lancement, Discord vous demande d'autoriser l'application : cliquez sur « Autoriser » "
        "dans la fenêtre de Discord.",
    "The tray icon shows the state: green = mic live, red = muted or deafened, orange = waiting for GoXLR Utility "
    "or Discord, grey = action needed. Right-click it for options.":
        "L'icône indique l'état : vert = micro ouvert, rouge = micro coupé ou sourdine, orange = en attente de "
        "GoXLR Utility ou de Discord, gris = action nécessaire. Clic droit pour les options.",
    "Launch now": "Lancer maintenant",
    "Launch": "Lancement",
    "Could not start the application:": "Impossible de lancer l'application :",
    "Launched": "Lancée",
    "GoXLR Discord Sync is running. Look for its icon in the system tray.":
        "GoXLR Discord Sync est lancé. Cherchez son icône dans la zone de notification.",
    "Uninstall": "Désinstallation",
    "Stop GoXLR Discord Sync, remove the autostart and delete the application?":
        "Arrêter GoXLR Discord Sync, supprimer son démarrage automatique et effacer l'application ?",
}
