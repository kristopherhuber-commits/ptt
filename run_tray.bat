@echo off
cd /d "%~dp0"
powershell -Command "Start-Process .venv\Scripts\pythonw.exe -ArgumentList 'app\ptt_tray.py' -WorkingDirectory '%cd%' -Verb RunAs"
