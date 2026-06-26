@echo off
:: Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    goto :admin
) else (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:admin
cd /d "%~dp0"
echo Starting Push-to-Talk Dictation Installer (Elevated)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
