@echo off
echo === GoXLR Discord Sync Installation ===
echo.

:: Install dependencies
echo Installing Python modules...
pip install -r requirements.txt
echo.

:: Create shortcut in Startup folder
echo Setting up auto-start...
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

:: Check if exe exists, otherwise use Python script
if exist "%~dp0dist\GoXLR_Discord_Sync.exe" (
    set "SCRIPT_PATH=%~dp0dist\GoXLR_Discord_Sync.exe"
    echo Using compiled executable...

    :: Create VBS file to launch exe without window
    echo Set WshShell = CreateObject("WScript.Shell") > "%STARTUP%\GoXLR_Discord_Sync.vbs"
    echo WshShell.Run """%SCRIPT_PATH%""", 0, False >> "%STARTUP%\GoXLR_Discord_Sync.vbs"
) else (
    set "SCRIPT_PATH=%~dp0goxlr_discord_sync.pyw"
    echo Using Python script...
    echo WARNING: For best experience, run build.bat first to create an exe.
    echo:

    :: Create VBS file to launch script without window
    echo Set WshShell = CreateObject("WScript.Shell") > "%STARTUP%\GoXLR_Discord_Sync.vbs"
    echo WshShell.Run "pythonw ""%SCRIPT_PATH%""", 0, False >> "%STARTUP%\GoXLR_Discord_Sync.vbs"
)

echo.
echo === Installation complete ===
echo.
echo The script will start automatically when Windows boots.
echo.
echo IMPORTANT: Before the first automatic launch,
echo run the script manually once to configure Discord:
echo   python goxlr_discord_sync.pyw
echo.
pause
