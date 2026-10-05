@echo off
REM Manga AI Editor - updater. v2
REM Updates CODE ONLY (app behaves the same, data untouched). Safe to re-run.
REM Preserved forever: .env, config.json, providers_config.json, output/,
REM .venv, fonts/_user uploads, install.log, server_*.txt
REM How it finds the new version (first that works wins):
REM   1) MangaAI-worker.zip next to this file (offline, from supervisor)
REM   2) Live VERSION check against our GitHub repo, then branch download
set "REPO=lamineheskoura/ai-translator-rmtl"
chcp 65001 >nul 2>nul
cd /d "%~dp0"
setlocal EnableDelayedExpansion
set "LOG=%~dp0update.log"
echo ==== MangaAI Updater - %DATE% %TIME% ==== > "%LOG%"
echo ==== MangaAI Updater ====
set "UPDATE_URL=https://github.com/lamineheskoura/ai-translator-rmtl/releases/latest/download/MangaAI-worker.zip"
set "BRANCH_URL=https://github.com/lamineheskoura/ai-translator-rmtl/archive/refs/heads/main.zip"
set "VER_URL=https://raw.githubusercontent.com/lamineheskoura/ai-translator-rmtl/main/VERSION"
set "ZIP=%TEMP%\MangaAI-update.zip"
set "STAGE=%TEMP%\MangaAI-update"
set "SRC="

if exist "MangaAI-worker.zip" (
  echo [*] Using local MangaAI-worker.zip - offline update.
  copy /y "MangaAI-worker.zip" "%ZIP%" >nul
  set "SRC=MangaAI"
) else (
  echo [*] Checking our GitHub repo for a newer version ...
  set "REMVER="
  for /f "delims=" %%V in ('curl.exe -s --max-time 15 "%VER_URL%" 2^>nul') do set "REMVER=%%V"
  set /p OLDVER=<VERSION
  if defined REMVER (
    echo     Installed: !OLDVER!   Repo: !REMVER!
    if "!OLDVER!"=="!REMVER!" (
      echo [+] Already on the latest version. Nothing to do.
      pause & exit /b 0
    )
  ) else (
    echo     Could not reach GitHub - trying full download anyway ...
  )
  echo [*] Downloading latest code from GitHub ...
  powershell -NoProfile -Command "Invoke-WebRequest -Uri $env:BRANCH_URL -OutFile $env:ZIP" >> "%LOG%" 2>&1
  if not exist "%ZIP%" (
    echo [!] Download failed. Check internet, or place a newer MangaAI-worker.zip
    echo     next to update.bat and re-run. See update.log
    pause & exit /b 1
  )
  set "SRC=BRANCH"
)
for %%S in ("%ZIP%") do if %%~zS LSS 100000 (
  echo [!] Downloaded file too small - bad download. Delete it and retry.
  pause & exit /b 1
)
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%" >nul 2>nul
powershell -NoProfile -Command "Expand-Archive '%ZIP%' -DestinationPath $env:STAGE -Force" >> "%LOG%" 2>&1
if "%SRC%"=="BRANCH" (
  if not exist "%STAGE%\ai-translator-rmtl-main\main.py" (
    echo [!] Package looks broken - aborting, nothing changed.
    pause & exit /b 1
  )
  set "PKGBASE=%STAGE%\ai-translator-rmtl-main"
) else (
  if not exist "%PKGBASE%\main.py" (
    echo [!] Package looks broken - aborting, nothing changed.
    pause & exit /b 1
  )
  set "PKGBASE=%STAGE%\MangaAI"
)
for /f "delims=" %%V in (%PKGBASE%\VERSION) do set "NEWVER=%%V"
set /p OLDVER=<VERSION
echo [*] Current: !OLDVER!  --^>  New: !NEWVER!
if "!OLDVER!"=="!NEWVER!" (
  echo     Same version. Continue anyway? [Y/n]
  set /p GO=
  if /i "!GO!"=="n" goto done
)
echo [*] Backing up current code to _backup-v!OLDVER! ...
if exist "_backup-v!OLDVER!" rmdir /s /q "_backup-v!OLDVER!"
mkdir "_backup-v!OLDVER!" >nul 2>nul
for %%D in (editor scraper translator) do (
  if exist "%%D" robocopy "%%D" "_backup-v!OLDVER!\%%D" /E /XD __pycache__ >nul
)
for %%F in (main.py requirements.txt install.bat start_server.bat update.bat package.bat smoke.bat .env.example .gitignore README.md README-WORKERS.txt VERSION CONTRACTS.md) do (
  if exist "%%F" copy /y "%%F" "_backup-v!OLDVER!\" >nul
)
echo [*] Applying new code (data folders untouched) ...
robocopy "%PKGBASE%\editor" "editor" /E /XD __pycache__ >nul
robocopy "%PKGBASE%\scraper" "scraper" /E /XD __pycache__ >nul
robocopy "%PKGBASE%\translator" "translator" /E /XD __pycache__ /XF providers_config.json >nul
for %%F in (main.py requirements.txt install.bat start_server.bat update.bat package.bat smoke.bat .env.example .gitignore README.md README-WORKERS.txt VERSION CONTRACTS.md) do (
  if exist "%PKGBASE%\%%F" copy /y "%PKGBASE%\%%F" . >nul
)
REM bundled fonts: add new ones, never delete worker uploads
if exist "%PKGBASE%\fonts\_user\*.ttf" copy /y "%PKGBASE%\fonts\_user\*.ttf" "fonts\_user\" >nul
if exist "%PKGBASE%\fonts\_user\*.otf" copy /y "%PKGBASE%\fonts\_user\*.otf" "fonts\_user\" >nul
echo [*] Refreshing libraries (fast, cached) ...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pip install --retries 3 --timeout 60 -r requirements.txt >> "%LOG%" 2>&1
  if errorlevel 1 (echo [!] pip refresh had errors - app may still work. See update.log.)
) else (
  echo [!] No .venv found - run install.bat first.
  pause & exit /b 1
)
rmdir /s /q "%STAGE%"
del "%ZIP%" 2>nul
echo.
echo [+] Updated to !NEWVER!. Your chapters, keys and settings are untouched.
echo     Old code backup: _backup-v!OLDVER!\  (delete it when all is fine)
echo     Now launch start_server.bat
:done
pause
exit /b 0
