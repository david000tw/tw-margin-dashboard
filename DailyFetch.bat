@echo off
REM DailyFetch.bat
REM Windows Task Scheduler calls this to run Claude /daily-fetch.
REM No arguments = fill from merged latest+1 to today-1.

setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist logs mkdir logs

REM Get today's date as YYYY-MM-DD via PowerShell (locale-independent)
for /f "delims=" %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%D

set LOG=logs\daily-fetch-%TODAY%.log

echo. >> "%LOG%"
echo ============================================ >> "%LOG%"
echo [%TODAY% %time%] START /daily-fetch >> "%LOG%"
echo ============================================ >> "%LOG%"

REM Verify claude is in PATH
where claude >nul 2>&1
if errorlevel 1 (
  echo [ERROR] 'claude' not found in PATH. >> "%LOG%"
  echo [%TODAY% %time%] FAILED rc=127 log=%LOG% >> logs\alerts.log
  exit /b 127
)

REM --permission-mode bypassPermissions: non-interactive, no prompts
claude -p "/daily-fetch" --permission-mode bypassPermissions >> "%LOG%" 2>&1
set RC=!errorlevel!

echo [%TODAY% %time%] END exit=!RC! >> "%LOG%"

if !RC! neq 0 (
  echo [%TODAY% %time%] FAILED rc=!RC! log=%LOG% >> logs\alerts.log
)

exit /b !RC!
