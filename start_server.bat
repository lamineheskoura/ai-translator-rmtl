@echo off
REM Manga AI Editor - daily launcher. Runs in the foreground so errors stay visible.
chcp 65001 >nul 2>nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [!] Not installed yet. Double-click install.bat first - once - then this file.
  pause & exit /b 1
)
".venv\Scripts\python.exe" -u main.py %*
if errorlevel 1 (
  echo.
  echo [!] Server stopped with an error. If this repeats, re-run install.bat to repair.
  pause
)
