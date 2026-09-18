@echo off
rem --- keep this file ASCII-only to avoid code page problems ---
title toio keyboard driver
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-toio.ps1"
echo.
echo Server stopped. Press any key to close this window.
pause > nul
