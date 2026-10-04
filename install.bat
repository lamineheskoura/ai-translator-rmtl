@echo off
REM Manga AI Editor - first-time installer (zero-experience friendly). v4
REM Finds REAL Python 3.10+ (Store stubs ignored, well-known install paths
REM probed directly so a just-finished install is found without PATH refresh),
REM otherwise installs it (winget, else python.org), then venv + requirements
REM + scraper browser + .env. Full log: install.log. Safe to re-run.
cd /d "%~dp0"
setlocal EnableDelayedExpansion
set "LOG=%~dp0install.log"
echo ==== MangaAI Installer v4 - %DATE% %TIME% ==== > "%LOG%"
echo ==== MangaAI Installer v4 ====
echo     Keep this window open. Everything is automatic.

call :find_python
if defined PY goto have_py

echo [*] No working Python found. Installing Python automatically ...
where winget >nul 2>nul
if %errorlevel%==0 (
  echo [*] Trying winget, this can take a few minutes ...
  winget install -e --id Python.Python.3.12 --silent --disable-interactivity --accept-source-agreements --accept-package-agreements >> "%LOG%" 2>&1
  echo     winget exit code: %errorlevel%>> "%LOG%"
  call :find_python
)
if not defined PY (
  echo [*] Downloading official Python installer (~30MB) ...
  set "PYSETUP=%TEMP%\python-setup.exe"
  powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $env:PYSETUP" >> "%LOG%" 2>&1
  if not exist "%TEMP%\python-setup.exe" goto no_python
  echo [*] Installing Python silently (2-4 minutes, no clicks needed) ...
  "%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 >> "%LOG%" 2>&1
  echo     setup exit code: %errorlevel%>> "%LOG%"
  call :find_python
)
if not defined PY goto no_python

:have_py
echo [+] Python found:
%PY% --version
%PY% --version >> "%LOG%" 2>&1
goto setup_venv

REM ---------- subroutine: locate working python ----------
:find_python
set "PY="
rem 1) py launcher, newest suitable backend first
where py >nul 2>nul
if %errorlevel%==0 (
  for %%V in (3.12 3.11 3.10 3) do (
    py -%%V -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
    if !errorlevel!==0 set "PY=py -%%V" & goto :eof
  )
)
rem 2) well-known install locations (works even when PATH is stale)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "CAND=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "CAND=%LOCALAPPDATA%\Programs\Python\Python311\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "CAND=%LOCALAPPDATA%\Programs\Python\Python310\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%ProgramFiles%\Python312\python.exe" set "CAND=%ProgramFiles%\Python312\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%ProgramFiles%\Python311\python.exe" set "CAND=%ProgramFiles%\Python311\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%ProgramFiles%\Python310\python.exe" set "CAND=%ProgramFiles%\Python310\python.exe" & call :try_cand & if defined PY goto :eof
rem 3) anything called python on PATH except Microsoft-Store stubs
for /f "delims=" %%P in ('where python 2^>nul') do (
  echo %%P | find /i "WindowsApps" >nul
  if errorlevel 1 (
    "%%P" -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
    if !errorlevel!==0 for %%I in ("%%P") do set "PY=%%~sI" & goto :eof
  ) else (
    echo [i] Ignoring fake Store stub: %%P>> "%LOG%"
    echo [i] Ignoring fake Store stub: %%P
  )
)
goto :eof

:try_cand
"%CAND%" -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if %errorlevel%==0 for %%I in ("%CAND%") do set "PY=%%~sI"
goto :eof

:no_python
echo [!] Automatic install failed. Two easy options: | tee_fail
echo     1) Open Microsoft Store, search "Python 3.12", install, re-run. | tee_fail
echo     2) Install from https://www.python.org/downloads/ (tick Add to PATH). | tee_fail
echo     Full log: install.log - send its last 30 lines to support. | tee_fail
set /p OPENSTORE=Open the Microsoft Store now? (Y/n):
if /i "%OPENSTORE%"=="n" goto endfail
start ms-windows-store://search/?query=Python 3.12
:endfail
pause & exit /b 1
:tee_fail
set /p L=
echo %L%
echo %L%>> "%LOG%"
goto :eof

REM ---------- Step 1: venv ----------
:setup_venv
if not exist ".venv\Scripts\python.exe" (
  echo [*] Creating virtual environment .venv ...
  %PY% -m venv .venv >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo [!] Failed to create venv. See install.log
    pause & exit /b 1
  )
)

REM ---------- Step 2: requirements ----------
echo [*] Installing requirements (takes a few minutes) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [!] pip install failed. Check internet, then re-run install.bat
  pause & exit /b 1
)

REM ---------- Step 3: scraper browser ----------
echo [*] Downloading scraper browser (one time, ~150MB) ...
".venv\Scripts\scrapling.exe" install --force >> "%LOG%" 2>&1
if errorlevel 1 (echo [!] Browser download failed - will retry automatically later.)

REM ---------- Step 4: .env ----------
if not exist ".env" (
  echo [*] Creating .env from template ...
  copy /y ".env.example" ".env" >nul
  echo     The translation key is entered inside the app (API button).
)
echo.
echo [+] Done. Double-click start_server.bat to launch.
pause
exit /b 0
