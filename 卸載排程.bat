@echo off
setlocal

set TASK1=FaRenRiZiLiao_Daily_Primary
set TASK2=FaRenRiZiLiao_Daily_Fallback

echo Uninstalling scheduled tasks...
echo.
schtasks /delete /tn "%TASK1%" /f
set RC1=%errorlevel%
echo   %TASK1% exit code = %RC1%

schtasks /delete /tn "%TASK2%" /f
set RC2=%errorlevel%
echo   %TASK2% exit code = %RC2%

echo.
echo Done. exit code 1 usually means task did not exist (safe to ignore).
echo.
pause
