@echo off
REM DailyFetch.bat
REM Windows Task Scheduler calls this to run Claude /daily-fetch.
REM No arguments = fill from merged latest+1 to today-1.

setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist logs mkdir logs

REM Get today as YYYY-MM-DD (locale-independent)
for /f "delims=" %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%D
REM Get timestamp HHMMSS for unique log file per run
for /f "delims=" %%T in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set TS=%%T

set LOG=logs\daily-fetch-%TODAY%-%TS%.log

echo [%TODAY% %time%] START /daily-fetch > "%LOG%"

REM Verify claude is in PATH
where claude >nul 2>&1
if errorlevel 1 (
  echo [ERROR] 'claude' not found in PATH. >> "%LOG%"
  echo [%TODAY% %time%] FAILED rc=127 log=%LOG% >> logs\alerts.log
  exit /b 127
)

claude -p "/daily-fetch" --permission-mode bypassPermissions >> "%LOG%" 2>&1
set RC=!errorlevel!

echo [%TODAY% %time%] END exit=!RC! >> "%LOG%"

if !RC! neq 0 (
  echo [%TODAY% %time%] FAILED rc=!RC! log=%LOG% >> logs\alerts.log
)

REM Log rotation: keep only last 60 log files
powershell -NoProfile -Command "Get-ChildItem 'logs\daily-fetch-*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 60 | Remove-Item -Force" 2>nul

exit /b !RC!
