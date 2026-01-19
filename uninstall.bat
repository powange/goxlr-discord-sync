@echo off
title GoXLR Discord Sync - Uninstall
echo === GoXLR Discord Sync Uninstallation ===
echo:

:: Pause at start to prevent instant close on error
timeout /t 1 /nobreak >nul

:: Stop running process
echo Stopping GoXLR Discord Sync if running...
taskkill /F /IM pythonw.exe /FI "WINDOWTITLE eq GoXLR*" 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq GoXLR*" 2>nul
timeout /t 2 /nobreak >nul
echo:

:: Remove auto-start VBS script
echo Removing auto-start...
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\GoXLR_Discord_Sync.vbs" (
    del "%STARTUP%\GoXLR_Discord_Sync.vbs"
    echo Auto-start removed.
) else (
    echo Auto-start not found already removed.
)
echo:

:: Ask if user wants to remove configuration files
echo Do you want to remove configuration files?
echo (client_id.txt, client_secret.txt, discord_token.json)
echo:
set /p remove_config="Remove config files? (y/N): "

if /i "%remove_config%"=="y" (
    echo:
    echo Removing configuration files...
    if exist "%~dp0client_id.txt" del "%~dp0client_id.txt"
    if exist "%~dp0client_secret.txt" del "%~dp0client_secret.txt"
    if exist "%~dp0discord_token.json" del "%~dp0discord_token.json"
    echo Configuration files removed.
) else (
    echo Configuration files kept.
)
echo:

:: Ask if user wants to uninstall Python modules
echo:
echo Do you want to uninstall Python modules?
echo (websockets, pypresence, requests, pystray, Pillow)
echo WARNING: This may affect other Python projects!
echo:
set /p remove_modules="Uninstall Python modules? (y/N): "

if /i "%remove_modules%"=="y" (
    echo:
    echo Uninstalling Python modules...
    pip uninstall -y websockets pypresence requests pystray Pillow
    echo Modules uninstalled.
) else (
    echo Python modules kept.
)
echo:

echo:
echo === Uninstallation complete ===
echo:
echo You can now safely delete this folder.
echo:
echo Press any key to exit...
pause >nul
