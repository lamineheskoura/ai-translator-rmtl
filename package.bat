@echo off
REM Manga AI Editor - builds a clean worker package (no secrets, no 7GB output).
REM Output: MangaAI-worker.zip next to this file.
cd /d "%~dp0"
set "STAGE=%TEMP%\MangaAI-ship"
set "ZIP=%~dp0MangaAI-worker.zip"
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%\MangaAI" || (echo [!] Cannot create staging dir & pause & exit /b 1)

REM --- code + resources (never secrets) ---
for %%D in (editor scraper translator fonts) do (
  mkdir "%STAGE%\MangaAI\%%D" 2>nul
  robocopy "%%D" "%STAGE%\MangaAI\%%D" /E /XD __pycache__ _user /XF *.pyc *.bak >nul
)
REM --- user fonts ARE needed on new machines (Hayah etc.) ---
mkdir "%STAGE%\MangaAI\fonts\_user" 2>nul
robocopy "fonts\_user" "%STAGE%\MangaAI\fonts\_user" *.ttf *.otf .gitkeep >nul
REM --- entry + setup files ---
for %%F in (main.py requirements.txt install.bat start_server.bat .env.example .gitignore README-WORKERS.txt) do (
  if exist "%%F" copy /y "%%F" "%STAGE%\MangaAI\" >nul
)
REM --- empty runtime folders (created with .gitkeep so structure is exact) ---
mkdir "%STAGE%\MangaAI\output" 2>nul
copy /y NUL "%STAGE%\MangaAI\output\.gitkeep" >nul
if exist "translator\models\.gitkeep" copy /y "translator\models\.gitkeep" "%STAGE%\MangaAI\translator\models\" >nul

REM --- safety: delete anything secret that slipped in ---
del /q "%STAGE%\MangaAI\.env" 2>nul
del /q "%STAGE%\MangaAI\translator\providers\providers_config.json" 2>nul
del /q "%STAGE%\MangaAI\config.json" 2>nul

if exist "%ZIP%" del /q "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path '%STAGE%\MangaAI' -DestinationPath '%ZIP%' -Force"
if errorlevel 1 (echo [!] Zip failed & pause & exit /b 1)
rmdir /s /q "%STAGE%"
echo.
echo [+] Package ready: %ZIP%
dir "%ZIP%"
pause
