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
REM Self-heal step 0: a newer updater saved as update.new.bat takes over
REM automatically (no rename needed, no reinstall ever).
if exist "update.new.bat" (
  fc /b "update.new.bat" "update.bat" >nul 2>nul
  if errorlevel 1 (
    echo [*] Switching to the newer updater ...
    copy /y "update.new.bat" "update.bat" >nul
    del "update.new.bat" 2>nul
    call "%~f0"
    exit /b %errorlevel%
  ) else (
    del "update.new.bat" 2>nul
  )
)
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
  if not exist "%STAGE%\MangaAI\main.py" (
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
set "MANIFEST=%PKGBASE%\MANIFEST.txt"
set "LEGACY_MODE="
if not exist "%MANIFEST%" (
  echo [!] Package has no MANIFEST.txt - using built-in legacy list instead.
  echo #legacy > "%TEMP%\MangaAI-legacy-manifest.txt"
  echo D editor>> "%TEMP%\MangaAI-legacy-manifest.txt"
  echo D scraper>> "%TEMP%\MangaAI-legacy-manifest.txt"
  echo D translator>> "%TEMP%\MangaAI-legacy-manifest.txt"
  echo D fonts>> "%TEMP%\MangaAI-legacy-manifest.txt"
  for %%F in (main.py requirements.txt install.bat start_server.bat update.bat package.bat smoke.bat .env.example .gitignore README.md README-WORKERS.txt VERSION MANIFEST.txt CONTRACTS.md) do echo F %%F>> "%TEMP%\MangaAI-legacy-manifest.txt"
  set "MANIFEST=%TEMP%\MangaAI-legacy-manifest.txt"
  set "LEGACY_MODE=1"
)
echo [*] Backing up current code to _backup-v!OLDVER! ...
if exist "_backup-v!OLDVER!" rmdir /s /q "_backup-v!OLDVER!"
mkdir "_backup-v!OLDVER!" >nul 2>nul
for /f "tokens=1* delims= " %%A in ('findstr /b "D " "%MANIFEST%"') do (
  if exist "%%B" robocopy "%%B" "_backup-v!OLDVER!\%%B" /E /XD __pycache__ >nul
)
for /f "tokens=1* delims= " %%A in ('findstr /b "F " "%MANIFEST%"') do (
  if exist "%%B" copy /y "%%B" "_backup-v!OLDVER!\" >nul
)
echo [*] Applying new code - manifest-driven, data folders untouched ...
for /f "tokens=1* delims= " %%A in ('findstr /b "D " "%MANIFEST%"') do (
  if not exist "%PKGBASE%\%%B\*" (
    echo     [!] Package missing dir %%B - skipping it, local copy kept.
  ) else if "%%B"=="translator" (
    robocopy "%PKGBASE%\%%B" "%%B" /MIR /XD __pycache__ /XF providers_config.json >nul
  ) else if "%%B"=="fonts" (
    robocopy "%PKGBASE%\%%B" "%%B" /MIR /XD __pycache__ _user /XF *.ttf *.otf >nul
    if exist "%PKGBASE%\fonts\_user\*.ttf" copy /y "%PKGBASE%\fonts\_user\*.ttf" "fonts\_user\" >nul
    if exist "%PKGBASE%\fonts\_user\*.otf" copy /y "%PKGBASE%\fonts\_user\*.otf" "fonts\_user\" >nul
  ) else (
    if not exist "%%B" mkdir "%%B" >nul 2>nul
    robocopy "%PKGBASE%\%%B" "%%B" /MIR /XD __pycache__ >nul
  )
)
for /f "tokens=1* delims= " %%A in ('findstr /b "F " "%MANIFEST%"') do (
  if exist "%PKGBASE%\%%B" (
    if /i "%%B"=="update.bat" (
      copy /y "%PKGBASE%\%%B" "update.new.bat" >nul
      set "SELFUPDATED=1"
    ) else (
      copy /y "%PKGBASE%\%%B" . >nul
    )
  )
)
REM /MIR above already removed local code files deleted upstream.
REM Data is never mirrored: providers_config.json, _user uploads, output, .venv.
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
if defined SELFUPDATED (
  echo     NOTE: the updater itself was refreshed too - it activates
  echo     automatically next time you run update.bat. Nothing to rename.
)
echo     Now launch start_server.bat
:done
pause
exit /b 0
