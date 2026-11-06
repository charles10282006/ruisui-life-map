@echo off
rem Ruisui medical map: daily refresh (NHI clinics and hours, AED, bus, TRA 7-day timetable).
rem Schedule with Windows Task Scheduler, e.g. every day at 08:00.
rem Keep this file ASCII-only: cmd.exe reads .bat files in the system code page.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo [%date% %time%] daily start >> data\update.log
python scripts\fetch_daily.py >> data\update.log 2>&1
python scripts\build_facilities.py >> data\update.log 2>&1
python scripts\fetch_transit.py >> data\update.log 2>&1
echo [%date% %time%] daily done >> data\update.log
