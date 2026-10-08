@echo off
REM Manga AI Editor - smoke test. Must be GREEN before any commit/push.
REM Checks: imports, JS syntax, route contract, export, data schema.
chcp 65001 >nul 2>nul
cd /d "%~dp0"
set "FAIL=0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo [smoke 1/7] imports ...
%PY% -c "import editor.server, editor.exporter, scraper.coordinator, scraper.spider, scraper.series, translator.providers; print('imports OK')" 2>&1 | find "imports OK" >nul
if errorlevel 1 (echo FAIL: imports & set "FAIL=1") else (echo ok: imports)
echo [smoke 2/7] JS syntax ...
node --check editor/static/app.js >nul 2>&1
if errorlevel 1 (echo FAIL: node --check & set "FAIL=1") else (echo ok: js)
echo [smoke 3/7] route contract - cancel/retry must match server ...
find "api/queue/cancel/" editor/static/app.js >nul
if errorlevel 1 (echo FAIL: cancel route & set "FAIL=1") else (echo ok: cancel route)
find "api/batch/" editor/static/app.js | find "retry-failed" >nul
if errorlevel 1 (echo FAIL: retry route & set "FAIL=1") else (echo ok: retry route)
echo [smoke 4/7] export one page ...
set "SMOKE_CH="
for /f "delims=" %%C in ('dir /b /a:d "output" 2^>nul') do (
  for /f "delims=" %%H in ('dir /b /a:d "output\%%C" 2^>nul') do (
    if exist "output\%%C\%%H\chapter_data.json" set "SMOKE_CH=output\%%C\%%H"
  )
)
if not defined SMOKE_CH (echo SKIP: no chapter data) else (
  if exist "%TEMP%\smoke-export" rmdir /s /q "%TEMP%\smoke-export"
  %PY% -c "from pathlib import Path; import os; from editor.exporter import export_chapter; ch=Path(r'%SMOKE_CH%'); out=Path(os.environ['TEMP'])/'smoke-export'; fs=export_chapter(ch,out,fmt='webp',page_range=[1]); print('exported:',fs)" >nul 2>&1
  if errorlevel 1 (echo FAIL: export & set "FAIL=1") else (echo ok: export)
)
echo [smoke 5/7] chapter schema ...
if not defined SMOKE_CH (echo SKIP: no chapter data) else (
  %PY% -c "import json; d=json.load(open(r'%SMOKE_CH%\chapter_data.json',encoding='utf-8')); t=next(t for p in d['pages'] for t in p.get('texts',[]) if t.get('id')); need={'id','page','x','y','width','height','font_size_px','style','original_text','arabic_text'}; sn={'font','font_size','color','stroke_color','stroke_width','stroke_enabled','align'}; m=need-set(t); sm=sn-set(t.get('style',{})); assert not m and not sm, (m,sm); print('schema OK')" >nul 2>&1
  if errorlevel 1 (echo FAIL: schema & set "FAIL=1") else (echo ok: schema)
)
echo [smoke 6/7] pitch parity ...
%PY% tests/test_pitch_parity.py
if errorlevel 1 (echo FAIL: pitch parity & set "FAIL=1") else (echo ok: pitch parity)
echo [smoke 7/7] h0 bias ...
%PY% tests/test_h0_bias.py
if errorlevel 1 (echo FAIL: h0 bias & set "FAIL=1") else (echo ok: h0 bias)
echo.
if "%FAIL%"=="1" (echo SMOKE: RED - do not commit & pause & exit /b 1)
echo SMOKE: GREEN - safe to commit.
