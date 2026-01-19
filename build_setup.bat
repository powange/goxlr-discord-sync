@echo off
title Build Setup Wizard
echo === Building Setup Wizard ===
echo:

:: Check if PyInstaller is installed
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
    echo:
)

:: Clean previous build
if exist "dist\GoXLR_Setup.exe" (
    echo Cleaning previous setup build...
    del "dist\GoXLR_Setup.exe"
)
echo:

:: Check if GoXLR_Discord_Sync.exe exists
if not exist "dist\GoXLR_Discord_Sync.exe" (
    echo ERROR: GoXLR_Discord_Sync.exe not found!
    echo Please run build.bat first to create the main executable.
    pause
    exit /b 1
)

:: Stop running setup before build
echo Stopping GoXLR_Setup if running...
taskkill /F /IM GoXLR_Setup.exe 2>nul
timeout /t 2 /nobreak >nul

:: Build setup wizard executable with bundled files
echo Building setup wizard executable...
echo Bundling: GoXLR_Discord_Sync.exe and requirements.txt
python -m PyInstaller --onefile --windowed --name "GoXLR_Setup" --add-data "dist\GoXLR_Discord_Sync.exe;." --add-data "requirements.txt;." setup_gui.py

echo:
if exist "dist\GoXLR_Setup.exe" (
    echo === Setup wizard build successful! ===
    echo:
    echo Executable: dist\GoXLR_Setup.exe
    echo:
    echo You can distribute this single file to install GoXLR Discord Sync.
) else (
    echo === Build failed! ===
    echo Check the errors above.
)

echo:
if "%1" neq "nobatch" (
    echo Press any key to exit...
    pause >nul
)
