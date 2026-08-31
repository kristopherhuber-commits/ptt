@echo off
:: install.bat -- the one thing in the extracted folder meant to be clicked.

:: The installer verifies the whole payload, and cannot verify itself. If
:: install.ps1 did not survive extraction, this script hands PowerShell 12 KB of
:: NUL bytes and PowerShell reports "The term '' is not recognized" at line 1,
:: character 1 -- which is a true statement about an empty command and tells
:: nobody anything. That was the v3.0 bug report.
::
:: findstr matches the first line of install.ps1, which is the literal below.
:: A blank or truncated file fails it. Checked before elevation, so a broken
:: extraction does not cost the user a UAC prompt first.
findstr /b /c:"# install.ps1" "%~dp0_internal\install.ps1" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   The installer script did not survive being extracted.
    echo.
    echo   The download is almost certainly fine. Windows Explorer's
    echo   "Extract All" loses files from an archive this large, and
    echo   gives no error when it does.
    echo.
    echo   Extract it again with Windows' own tar, which does not.
    echo   In the folder holding ptt_dictate_dist.zip, run:
    echo.
    echo       rmdir /s /q ptt_dictate_dist
    echo       mkdir ptt_dictate_dist
    echo       tar -xf ptt_dictate_dist.zip -C ptt_dictate_dist
    echo.
    echo   Then run install.bat from the new folder.
    echo.
    pause
    exit /b 1
)

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
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_internal\install.ps1"
pause
