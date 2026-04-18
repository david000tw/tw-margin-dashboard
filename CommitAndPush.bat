@echo off
cd /d "%~dp0"
echo ========================================
echo   Commit ^& Push 04-13 ~ 04-17 data
echo ========================================
echo.
git status
echo.
echo [1/3] git add -A
git add -A
if errorlevel 1 goto fail
echo.
echo [2/3] git commit
git commit -m "data: 04-13 to 04-17 (5 days missing catch-up)"
if errorlevel 1 goto fail_commit
echo.
echo [3/3] git push
git push
if errorlevel 1 goto fail_push
echo.
echo ========================================
echo   SUCCESS! Pushed to GitHub.
echo ========================================
goto end

:fail
echo.
echo [ERROR] git add failed.
goto end

:fail_commit
echo.
echo [NOTE] git commit returned an error.
echo   If it says "nothing to commit" that's fine.
echo   Otherwise, check the message above.
goto end

:fail_push
echo.
echo [ERROR] git push failed.
echo   - Check network connection
echo   - Check GitHub credentials (Fork / Git Credential Manager)
goto end

:end
echo.
pause
