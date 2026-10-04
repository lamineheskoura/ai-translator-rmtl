@echo off
REM Manga AI Editor - first-time installer (zero-experience friendly). v3
REM 1) Ensures real Python 3.10+ exists (ignores the fake Microsoft-Store
REM    stub, installs Python automatically if truly missing)
REM 2) Creates isolated .venv, installs requirements, downloads scraper browser
REM 3) Creates .env from template. Safe to re-run (updates only).
REM A full log is saved to install.log next to this file.
cd /d "%~dp0"
setlocal EnableDelayedExpansion
set "LOG=%~dp0install.log"
echo ==== MangaAI Installer v3 - %DATE% %TIME% ==== > "%LOG%"
echo ==== MangaAI Installer v3 ====
echo     (If a helper asks: this window installs the program. Keep it open.)

REM ---------- Step 0: find a REAL python (skip Store stub) ----------
set "PY="
call :find_python
if defined PY goto have_py

REM ---------- Step 0b: auto-install python ----------
echo [*] No working Python found. Installing Python automatically ... | tee_log
where winget >nul 2>nul
if %errorlevel%==0 (
  echo [*] Installing via winget (accept any admin prompt) ... | tee_log
  winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements >> "%LOG%" 2>&1
  call :find_python
)
if not defined PY (
  echo [*] Downloading official Python installer (~30MB) ... | tee_log
  set "PYSETUP=%TEMP%\python-setup.exe"
  powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $env:PYSETUP" >> "%LOG%" 2>&1
  if not exist "%TEMP%\python-setup.exe" goto no_python
  echo [*] Running Python setup silently ... | tee_log
  "%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 >> "%LOG%" 2>&1
  set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
  call :find_python
)
if not defined PY goto no_python

:have_py
echo [+] Python found: | tee_log
%PY% --version | tee_log
goto setup_venv

REM ---------- subroutine: locate working python ----------
:find_python
set "PY="
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
  if !errorlevel!==0 set "PY=py -3" & goto :eof
)
for /f "delims=" %%P in ('where python 2^>nul') do (
  echo %%P | find /i "WindowsApps" >nul
  if errorlevel 1 (
    "%%P" -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
    if !errorlevel!==0 set "PY=%%P" & goto :eof
  ) else (
    echo [i] Ignoring fake Store stub: %%P >> "%LOG%"
  )
)
goto :eof

:no_python
echo [!] Python 3.10+ is still missing. | tee_log
echo     Open the Microsoft Store, install "Python 3.12", then re-run install.bat | tee_log
echo     Or install from https://www.python.org/downloads/ (tick "Add to PATH"). | tee_log
echo     Full log: install.log | tee_log
pause & exit /b 1

REM ---------- Step 1: venv ----------
:setup_venv
if not exist ".venv\Scripts\python.exe" (
  echo [*] Creating virtual environment .venv ... | tee_log
  %PY% -m venv .venv >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo [!] Failed to create venv. See install.log | tee_log
    pause & exit /b 1
  )
)

REM ---------- Step 2: requirements ----------
echo [*] Installing requirements (takes a few minutes) ... | tee_log
".venv\Scripts\python.exe" -m pip install --upgrade pip >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [!] pip install failed. Check internet, then re-run install.bat | tee_log
  pause & exit /b 1
)

REM ---------- Step 3: scraper browser ----------
echo [*] Downloading scraper browser (one time, ~150MB) ... | tee_log
".venv\Scripts\scrapling.exe" install --force >> "%LOG%" 2>&1
if errorlevel 1 (echo [!] Browser download failed - will retry automatically later. | tee_log)

REM ---------- Step 4: .env ----------
if not exist ".env" (
  echo [*] Creating .env from template ... | tee_log
  copy /y ".env.example" ".env" >nul
  echo     The translation key is entered inside the app (API button). | tee_log
)
echo. | tee_log
echo [+] Done. Double-click start_server.bat to launch. | tee_log
pause
exit /b 0

:tee_log
set /p LINE=
echo %LINE%
echo %LINE%>> "%LOG%"
goto :eof
