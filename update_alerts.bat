@echo off
rem Ruisui evacuation routes: refresh live alerts (debris-flow alerts, highway events, CWA warnings).
rem Normal days: every 3 hours. During typhoon / heavy rain: every 30 minutes.
rem Keep this file ASCII-only: cmd.exe reads .bat files in the system code page.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo [%date% %time%] alerts >> data\update.log
python scripts\fetch_alerts.py >> data\update.log 2>&1
