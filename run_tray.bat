@echo off
cd /d "%~dp0"
powershell -Command "Start-Process .venv\Scripts\ptt_dictate.exe -ArgumentList 'app\ptt_tray.py' -WorkingDirectory '%cd%' -Verb RunAs"
