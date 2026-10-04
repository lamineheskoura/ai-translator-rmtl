@echo off
REM Manga AI Editor - first-time installer. v5
REM Steps: [1/5] environment check - [2/5] Python - [3/5] venv -
REM [4/5] requirements - [5/5] scraper browser + .env
REM Full log: install.log. Safe to re-run (repairs only what is broken).
chcp 65001 >nul 2>nul
cd /d "%~dp0"
setlocal EnableDelayedExpansion
set "LOG=%~dp0install.log"
echo ==== MangaAI Installer v5 - %DATE% %TIME% ==== > "%LOG%"
echo ==== MangaAI Installer v5 ====
echo     Keep this window open. Everything is automatic. (5 steps)

REM ---------- [1/5] diagnostics ----------
echo [1/5] Checking this computer ...
ver >> "%LOG%" 2>&1
echo ARCH=%PROCESSOR_ARCHITECTURE%>> "%LOG%"
whoami >> "%LOG%" 2>&1
powershell -NoProfile -Command "$d=(Get-PSDrive C).Free/1GB; \"FREE_GB=$([math]::Round($d,1))\"; if($d -lt 5){exit 1}else{exit 0}" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [!] Less than 5GB free on C:. Free space and re-run install.bat.
  echo     See install.log for details.
  pause & exit /b 1
)
if /i not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
  if /i not "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
    echo [!] Unsupported CPU %PROCESSOR_ARCHITECTURE%. 64-bit Windows required - stopping.
    pause & exit /b 1
  )
)
echo %OS% %PROCESSOR_ARCHITECTURE% | find /i "ARM64" >nul
if %errorlevel%==0 (
  set "PYARCH=arm64"
  set "PYURL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-arm64.exe"
) else (
  set "PYARCH=amd64"
  set "PYURL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
)

call :find_python
if defined PY goto have_py

REM ---------- [2/5] Python ----------
echo [2/5] Installing Python automatically ...
where winget >nul 2>nul
if %errorlevel%==0 (
  echo     Trying winget - minutes, needs internet ...
  winget install -e --id Python.Python.3.12 --silent --disable-interactivity --accept-source-agreements --accept-package-agreements >> "%LOG%" 2>&1
  call :find_python
)
if not defined PY (
  echo     Downloading official Python - once, ~30MB ...
  set "PYSETUP=%TEMP%\python-setup.exe"
  if exist "%TEMP%\python-setup.exe" del "%TEMP%\python-setup.exe"
  powershell -NoProfile -Command "Invoke-WebRequest -Uri '!PYURL!' -OutFile $env:PYSETUP" >> "%LOG%" 2>&1
  if not exist "%TEMP%\python-setup.exe" goto no_python
  for %%S in ("%TEMP%\python-setup.exe") do if %%~zS LSS 20000000 goto dl_bad
  echo     Installing silently - 2-4 minutes, no clicks ...
  "%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 AssociateFiles=0 >> "%LOG%" 2>&1
  call :find_python
)
if not defined PY goto no_python

:have_py
echo [+] Python ready:
%PY% --version
%PY% --version >> "%LOG%" 2>&1
goto setup_venv

:dl_bad
echo [!] Python download is incomplete (bad internet?). Delete, reconnect, re-run.
pause & exit /b 1

REM ---------- subroutine: locate working 64-bit python ----------
:find_python
set "PY="
where py >nul 2>nul
if %errorlevel%==0 (
  for %%V in (3.12 3.11 3.10 3) do (
    py -%%V -c "import sys; exit(0 if sys.version_info>=(3,10) and sys.maxsize>2**32 else 1)" >nul 2>&1
    if !errorlevel!==0 set "PY=py -%%V" & goto :eof
  )
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "CAND=%LOCALAPPDATA%\Programs\Python\Python312\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "CAND=%LOCALAPPDATA%\Programs\Python\Python311\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "CAND=%LOCALAPPDATA%\Programs\Python\Python310\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%ProgramFiles%\Python312\python.exe" set "CAND=%ProgramFiles%\Python312\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%ProgramFiles%\Python311\python.exe" set "CAND=%ProgramFiles%\Python311\python.exe" & call :try_cand & if defined PY goto :eof
if exist "%ProgramFiles%\Python310\python.exe" set "CAND=%ProgramFiles%\Python310\python.exe" & call :try_cand & if defined PY goto :eof
for /f "delims=" %%P in ('where python 2^>nul') do (
  echo %%P | find /i "WindowsApps" >nul
  if errorlevel 1 (
    "%%P" -c "import sys; exit(0 if sys.version_info>=(3,10) and sys.maxsize>2**32 else 1)" >nul 2>&1
    if !errorlevel!==0 for %%I in ("%%P") do set "PY=%%~sI" & goto :eof
  ) else (
    echo [i] Ignoring fake Store stub: %%P>> "%LOG%"
    echo [i] Ignoring fake Store stub: %%P
  )
)
goto :eof

:try_cand
"%CAND%" -c "import sys; exit(0 if sys.version_info>=(3,10) and sys.maxsize>2**32 else 1)" >nul 2>&1
if %errorlevel%==0 for %%I in ("%CAND%") do set "PY=%%~sI"
goto :eof

:no_python
echo [!] Automatic install failed. Easiest fix:
echo     1) Open Microsoft Store, search "Python 3.12", install it, re-run install.bat.
echo     2) Or install from https://www.python.org/downloads/
echo     Full log: install.log (send its last 30 lines to support).
set /p OPENSTORE=Open the Microsoft Store now? (Y/n):
if /i "%OPENSTORE%"=="n" goto endfail
start ms-windows-store://search/?query=Python 3.12
:endfail
pause & exit /b 1

REM ---------- Step 3: venv (health-checked, repairs broken ones) ----------
:setup_venv
set "VPY=.venv\Scripts\python.exe"
if exist "%VPY%" (
  "%VPY%" -c "import sys, ensurepip; import fastapi" >nul 2>&1
  if errorlevel 1 (
    echo [!] Old virtual env is broken. Rebuilding ...
    rmdir /s /q .venv
  )
)
if not exist "%VPY%" (
  echo [3/5] Creating virtual environment ...
  %PY% -m venv .venv >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo [!] Failed to create venv. See install.log
    pause & exit /b 1
  )
)

REM ---------- Step 4: requirements ----------
echo [4/5] Installing libraries (takes a few minutes, resume-safe) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --retries 3 --timeout 60 >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m pip install --retries 3 --timeout 60 -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [!] Library install failed - internet? proxy? space? Re-run install.bat to resume.
  echo     Behind a company proxy? Tell support the proxy address.
  pause & exit /b 1
)

REM ---------- Step 5: browser + .env ----------
echo [5/5] Downloading scraper browser (once, ~150MB) ...
".venv\Scripts\scrapling.exe" install --force >> "%LOG%" 2>&1
if errorlevel 1 (echo [!] Browser download failed - the app will retry it automatically later.)
if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo     The translation key is entered inside the app [API button].
)
echo.
echo [+] Done. Double-click start_server.bat to launch. (Step 5/5 complete)
pause
exit /b 0
