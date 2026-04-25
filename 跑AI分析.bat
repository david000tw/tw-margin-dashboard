@echo off
chcp 65001 > nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo.
echo ============================================================
echo  台股法人多 Agent 分析（走 Claude Code 訂閱，免 API key）
echo ============================================================
echo.

set DAYS=%1
if "%DAYS%"=="" set DAYS=30

python agents\analyze.py --days %DAYS%

if %errorlevel% neq 0 (
    echo.
    echo [X] 執行失敗，請看上方錯誤訊息
    pause
    exit /b %errorlevel%
)

echo.
echo 報告路徑：%cd%\agents\latest_report.md
echo.
pause
