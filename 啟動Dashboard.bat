@echo off
setlocal enableextensions
cd /d "%~dp0"

REM Find a free port between 8899-8910
set PORT=
for /l %%p in (8899,1,8910) do (
  if not defined PORT (
    powershell -NoProfile -Command "$c=New-Object Net.Sockets.TcpClient; try {$c.Connect('127.0.0.1',%%p); $c.Close(); exit 1} catch {exit 0}"
    if not errorlevel 1 set PORT=%%p
  )
)

if not defined PORT (
  echo [ERROR] No free port in 8899-8910.
  pause
  exit /b 1
)

echo Starting HTTP server on port %PORT% ...
start "" cmd /k "python -m http.server %PORT% --bind 127.0.0.1"
timeout /t 2 /nobreak >nul
start chrome "http://localhost:%PORT%/dashboard_all.html"
