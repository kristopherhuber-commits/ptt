@echo off
:: One level up: this file moved into _internal\ in v3.0 so that the
:: extracted download has exactly one executable in its root, which is
:: install.bat. Everything it launches still lives beside app\ and .venv\.
cd /d "%~dp0.."
powershell -Command "Start-Process .venv\Scripts\ptt_dictate.exe -ArgumentList 'app\ptt_tray.py' -WorkingDirectory $pwd.ProviderPath -Verb RunAs"
