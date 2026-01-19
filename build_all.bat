@echo off
title Build GoXLR Discord Sync - Complete Package
echo ========================================
echo Building Complete GoXLR Discord Sync
echo ========================================
echo:

:: Step 1: Build main application
echo [1/2] Building GoXLR_Discord_Sync.exe...
call build.bat nobatch
if errorlevel 1 (
    echo:
    echo ERROR: Failed to build main application!
    pause
    exit /b 1
)

echo:
echo:

:: Step 2: Build setup wizard
echo [2/2] Building GoXLR_Setup.exe...
call build_setup.bat nobatch
if errorlevel 1 (
    echo:
    echo ERROR: Failed to build setup!
    pause
    exit /b 1
)

echo:
echo:
echo ========================================
echo BUILD COMPLETE!
echo ========================================
echo:
echo Created files:
echo - dist\GoXLR_Discord_Sync.exe (Main application)
echo - dist\GoXLR_Setup.exe (Installer - standalone)
echo:
echo To distribute:
echo 1. Give users only GoXLR_Setup.exe
echo 2. They run it and follow the wizard
echo:
pause
