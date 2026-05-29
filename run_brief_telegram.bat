@echo off
title Market Brief → Telegram
color 0B
echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║     📊  MARKET BRIEF → TELEGRAM              ║
echo  ╚══════════════════════════════════════════════╝
echo.

cd /d "%~dp0"
if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe brief.py --telegram
) else (
    python brief.py --telegram
)

echo.
pause >nul
