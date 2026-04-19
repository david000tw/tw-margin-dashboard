@echo off
setlocal enableextensions
cd /d "%~dp0"

set BAT="%~dp0DailyFetch.bat"
set TASK1=FaRenRiZiLiao_Daily_Primary
set TASK2=FaRenRiZiLiao_Daily_Fallback

echo.
echo =================================================
echo  Register daily-fetch scheduled tasks
echo    Primary  : %TASK1%  (daily 23:30)
echo    Fallback : %TASK2%  (daily 07:00)
echo    Target   : %BAT%
echo =================================================
echo.

echo [1/2] creating %TASK1% ...
schtasks /create /tn "%TASK1%" /tr %BAT% /sc daily /st 23:30 /f
set RC1=%errorlevel%
echo     exit code = %RC1%
echo.

echo [2/2] creating %TASK2% ...
schtasks /create /tn "%TASK2%" /tr %BAT% /sc daily /st 07:00 /f
set RC2=%errorlevel%
echo     exit code = %RC2%
echo.

if not %RC1%==0 goto fail
if not %RC2%==0 goto fail

echo =================================================
echo  SUCCESS. Tasks registered.
echo.
echo  Verify:    schtasks /query /tn "%TASK1%"
echo  Trigger:   schtasks /run   /tn "%TASK1%"
echo  Uninstall: double-click uninstall bat file
echo =================================================
goto end

:fail
echo =================================================
echo  [FAILED] one or both schtasks calls returned non-zero.
echo  See output above. Common causes:
echo    - task already exists (run uninstall first)
echo    - schtasks prompted for password (local account w/ no pwd set)
echo    - path contains characters cmd cannot parse
echo.
echo  Manual fallback (copy-paste into cmd):
echo    schtasks /create /tn "%TASK1%" /tr %BAT% /sc daily /st 23:30 /f
echo    schtasks /create /tn "%TASK2%" /tr %BAT% /sc daily /st 07:00 /f
echo =================================================

:end
echo.
pause
