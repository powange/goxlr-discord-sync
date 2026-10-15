@echo off
title GoXLR Discord Sync - Build setup
python build.py setup
set RESULT=%errorlevel%
if not "%1"=="nobatch" pause
exit /b %RESULT%
