@echo off
REM Manga AI Editor - launcher. Uses .venv when present, system python otherwise.
REM Runs in the foreground so errors stay visible. Ctrl+C to stop.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
%PY% -u main.py %*
if errorlevel 1 (
  echo.
  echo [!] Server exited with an error. If packages are missing, run install.bat first.
  pause
)
