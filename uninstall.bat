@echo off
title GoXLR Discord Sync - Uninstall
echo === GoXLR Discord Sync uninstallation ===
echo.

echo Stopping GoXLR Discord Sync if running...
taskkill /F /IM GoXLR_Discord_Sync.exe >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*goxlr_discord_sync*' -and $_.Name -like 'python*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
timeout /t 2 /nobreak >nul

echo Removing autostart...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "GoXLR Discord Sync" /f >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\GoXLR_Discord_Sync.vbs" >nul 2>&1
echo Autostart removed.
echo.

set /p remove_config="Delete settings and Discord credentials (%APPDATA%\GoXLR Discord Sync)? (y/N): "
if /i "%remove_config%"=="y" (
    rmdir /s /q "%APPDATA%\GoXLR Discord Sync"
    echo Settings deleted.
) else (
    echo Settings kept.
)
echo.

echo Uninstalling the Python modules may affect other Python projects.
set /p remove_modules="Uninstall websockets, requests, pystray, Pillow? (y/N): "
if /i "%remove_modules%"=="y" (
    python -m pip uninstall -y websockets requests pystray Pillow
)
echo.
echo === Uninstallation complete. You can delete this folder. ===
pause
