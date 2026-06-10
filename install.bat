@echo off
:: install.bat - Bootstrap script to launch the PowerShell installer
cd /d "%~dp0"
echo Starting Push-to-Talk Dictation Installer...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
