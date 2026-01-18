@echo off
echo === Installation GoXLR Discord Sync ===
echo.

:: Installer les dépendances
echo Installation des modules Python...
pip install websockets
pip install pypresence
pip install requests
echo.

:: Créer le raccourci dans le dossier Startup
echo Creation du lancement automatique...
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SCRIPT_PATH=%~dp0goxlr_discord_sync.pyw"

:: Créer un fichier VBS pour lancer le script sans fenêtre
echo Set WshShell = CreateObject("WScript.Shell") > "%STARTUP%\GoXLR_Discord_Sync.vbs"
echo WshShell.Run "pythonw ""%SCRIPT_PATH%""", 0, False >> "%STARTUP%\GoXLR_Discord_Sync.vbs"

echo.
echo === Installation terminee ===
echo.
echo Le script se lancera automatiquement au demarrage de Windows.
echo.
echo IMPORTANT: Avant le premier lancement automatique,
echo lance le script manuellement une fois pour configurer Discord:
echo   python goxlr_discord_sync.pyw
echo.
pause
