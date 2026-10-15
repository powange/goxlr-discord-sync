@echo off
title GoXLR Discord Sync - Build all
python build.py all
set RESULT=%errorlevel%
if %RESULT%==0 (
    echo.
    echo Distribute dist\GoXLR_Setup.exe ^(checksums in dist\SHA256SUMS.txt^)
)
pause
exit /b %RESULT%
