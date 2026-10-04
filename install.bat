@echo off
REM Manga AI Editor - first-time installer for a new machine.
REM Creates an isolated venv, installs requirements, pre-downloads the
REM Scrapling browser used by the scraper. Safe to re-run (updates only).
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
  echo [*] Creating virtual environment .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (echo [!] Failed to create venv. Install Python 3.10+ from python.org & pause & exit /b 1)
)
echo [*] Installing requirements ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (echo [!] pip install failed. Check your internet connection. & pause & exit /b 1)
echo [*] Downloading scraper browser (one time, ~150MB) ...
".venv\Scripts\scrapling.exe" install --force
if errorlevel 1 (echo [!] Browser download failed - online scraping will retry it later.)
if not exist ".env" (
  echo [*] Creating .env from template ...
  copy /y ".env.example" ".env" >nul
  echo     Edit .env and add your API keys, then run start_server.bat
)
echo.
echo [+] Done. Launch with start_server.bat
pause
