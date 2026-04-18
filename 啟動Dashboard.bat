@echo off
cd /d "%~dp0"
start "" cmd /k "python -m http.server 8899 --bind 127.0.0.1"
timeout /t 2 /nobreak >nul
start chrome "http://localhost:8899/dashboard_all.html"
