@echo off
title Market Brief Engine
color 0A
echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║        📊  MARKET BRIEF ENGINE  v1.0         ║
echo  ╚══════════════════════════════════════════════╝
echo.
echo  Generando brief pre-market...
echo.

cd /d "%~dp0"
if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe brief.py --html
) else (
    python brief.py --html
)

echo.
echo  ═══════════════════════════════════════════════
echo  Presiona cualquier tecla para cerrar.
pause >nul
