@echo off
title GoXLR Discord Sync - Build
python build.py app
set RESULT=%errorlevel%
if not "%1"=="nobatch" pause
exit /b %RESULT%
