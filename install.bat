@echo off
REM Manga AI Editor - first-time installer (zero-experience friendly).
REM 1) Ensures Python 3.10+ exists (installs it automatically if missing)
REM 2) Creates isolated .venv, installs requirements, downloads scraper browser
REM 3) Creates .env from template. Safe to re-run (updates only).
cd /d "%~dp0"
setlocal EnableDelayedExpansion

REM ---------- Step 0: find python ----------
set "PY="
where py >nul 2>nul && (py -3 -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=py -3")
if not defined PY (
  where python >nul 2>nul && (python -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python")
)

REM ---------- Step 0b: auto-install python if missing ----------
if not defined PY (
  echo [*] Python not found. Installing Python automatically ...
  where winget >nul 2>nul
  if %errorlevel%==0 (
    echo [*] Installing via winget (may show an admin prompt - accept it) ...
    winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
  )
  where py >nul 2>nul && (py -3 -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=py -3")
  if not defined PY (
    echo [*] Downloading official Python installer ...
    set "PYSETUP=%TEMP%\python-setup.exe"
    powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $env:PYSETUP"
    if not exist "%TEMP%\python-setup.exe" (
      echo [!] Download failed. Install Python 3.10+ manually from:
      echo     https://www.python.org/downloads/
      echo     IMPORTANT: tick "Add python.exe to PATH" during setup.
      pause & exit /b 1
    )
    echo [*] Running Python setup (accept any prompt, keep PATH option checked) ...
    "%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
    where py >nul 2>nul && (py -3 -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=py -3")
    if not defined PY (
      where python >nul 2>nul && (python -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python")
    )
  )
)
if not defined PY (
  echo [!] Python 3.10+ is still missing. Install it from https://www.python.org/downloads/
  echo     (tick "Add python.exe to PATH") then re-run install.bat
  pause & exit /b 1
)
echo [+] Python found: & %PY% --version

REM ---------- Step 1: venv ----------
if not exist ".venv\Scripts\python.exe" (
  echo [*] Creating virtual environment .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (echo [!] Failed to create venv. & pause & exit /b 1)
)

REM ---------- Step 2: requirements ----------
echo [*] Installing requirements (takes a few minutes) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (echo [!] pip install failed. Check your internet connection. & pause & exit /b 1)

REM ---------- Step 3: scraper browser ----------
echo [*] Downloading scraper browser (one time, ~150MB) ...
".venv\Scripts\scrapling.exe" install --force
if errorlevel 1 (echo [!] Browser download failed - online scraping will retry it later.)

REM ---------- Step 4: .env ----------
if not exist ".env" (
  echo [*] Creating .env from template ...
  copy /y ".env.example" ".env" >nul
  echo     The translation key is entered from inside the app (API button),
  echo     no need to edit files manually.
)
echo.
echo [+] Done. Double-click start_server.bat to launch.
pause
