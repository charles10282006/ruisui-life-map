@echo off
rem Ruisui farm weather window: refresh CWA forecast.
rem CWA publishes 4 times a day; run every 6 hours (e.g. 05:30, 11:30, 17:30, 23:30).
rem Keep this file ASCII-only: cmd.exe reads .bat files in the system code page.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo [%date% %time%] weather >> data\update.log
python scripts\fetch_weather.py >> data\update.log 2>&1
