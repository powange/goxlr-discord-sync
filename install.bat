@echo off
title GoXLR Discord Sync - Install from source
echo === GoXLR Discord Sync installation (from source) ===
echo.
echo Installing Python modules...
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo ERROR: pip failed, see the messages above.
    pause
    exit /b 1
)
echo.
echo Enabling start with Windows...
python "%~dp0goxlr_discord_sync.pyw" --autostart on
if errorlevel 1 (
    echo ERROR: could not enable the autostart.
    pause
    exit /b 1
)
echo.
echo === Installation complete ===
echo Start it now with: pythonw goxlr_discord_sync.pyw
echo On first launch it opens the settings so you can enter your Discord application.
echo The graphical wizard (python setup_gui.py) does the same steps.
pause
