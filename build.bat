@echo off
title GoXLR Discord Sync - Build
echo === Building GoXLR Discord Sync ===
echo:

:: Check if PyInstaller is installed
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
    echo:
)

:: Stop running process before build
echo Stopping GoXLR_Discord_Sync if running...
taskkill /F /IM GoXLR_Discord_Sync.exe 2>nul
timeout /t 2 /nobreak >nul

:: Clean previous build (keep dist folder, only delete exe)
if exist "dist\GoXLR_Discord_Sync.exe" (
    echo Cleaning previous exe...
    del "dist\GoXLR_Discord_Sync.exe" 2>nul
    if exist "dist\GoXLR_Discord_Sync.exe" (
        echo ERROR: Cannot delete exe - please close the program first!
        pause
        exit /b 1
    )
)
if exist "build" (
    echo Cleaning build folder...
    rmdir /s /q build
)
if exist "*.spec" (
    del *.spec
)
echo:

:: Build executable
echo Building executable...
python -m PyInstaller --onefile --windowed --name "GoXLR_Discord_Sync" --icon=NONE goxlr_discord_sync.pyw

echo:
if exist "dist\GoXLR_Discord_Sync.exe" (
    echo === Build successful! ===
    echo:
    echo Executable: dist\GoXLR_Discord_Sync.exe
    echo:
    echo You can now:
    echo 1. Test the exe: dist\GoXLR_Discord_Sync.exe
    echo 2. Run install.bat to set up auto-start with the exe
) else (
    echo === Build failed! ===
    echo Check the errors above.
)

echo:
if "%1" neq "nobatch" (
    echo Press any key to exit...
    pause >nul
)
