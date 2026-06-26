# install.ps1
# Installer script for Push-to-Talk Dictation

$AppName = "PTT Dictation"
$SourceDir = $PSScriptRoot
$TargetParentDir = "$env:LOCALAPPDATA\Programs"
$TargetDir = "$TargetParentDir\ptt_dictate"

# Force output to use UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "          $AppName Installation Wizard            " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check if source files exist
if (-not (Test-Path "$SourceDir\.venv") -or -not (Test-Path "$SourceDir\app")) {
    Write-Host "Error: Installation source files not found!" -ForegroundColor Red
    Write-Host "Please ensure you have extracted all files from the ZIP before running this installer." -ForegroundColor Yellow
    Exit
}

# 2. Create target directory if it doesn't exist
if (-not (Test-Path $TargetParentDir)) {
    New-Item -ItemType Directory -Path $TargetParentDir -Force | Out-Null
}

# 3. Handle existing installation / locked files
if (Test-Path $TargetDir) {
    Write-Host "An existing installation was found. Attempting to close active instances..." -ForegroundColor Yellow
    # Try to close any running instances
    Stop-Process -Name "ptt_dictate" -Force -ErrorAction SilentlyContinue
    Stop-Process -Name "pythonw" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    
    Write-Host "Replacing old files with the new version..." -ForegroundColor Gray
    try {
        Remove-Item -Path $TargetDir -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Host "Warning: Some files are locked by another process. Overwriting files instead..." -ForegroundColor Yellow
    }
}

# 4. Copy files
Write-Host "Copying application files (this may take a minute)..." -ForegroundColor Gray
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null

Copy-Item -Path "$SourceDir\.venv" -Destination "$TargetDir\.venv" -Recurse -Container -Force
Copy-Item -Path "$SourceDir\app" -Destination "$TargetDir\app" -Recurse -Container -Force
Copy-Item -Path "$SourceDir\run_tray.bat" -Destination "$TargetDir\run_tray.bat" -Force

# Verify DLLs are in the Scripts directory of the target
$TargetScripts = "$TargetDir\.venv\Scripts"
if (Test-Path "$SourceDir\.venv\Scripts") {
    Get-ChildItem -Path "$SourceDir\.venv\Scripts\*.dll" | ForEach-Object {
        $dllName = $_.Name
        if (-not (Test-Path "$TargetScripts\$dllName")) {
            Copy-Item $_.FullName "$TargetScripts\$dllName" -Force
        }
    }
}

# 5. Create Desktop Shortcut
$DesktopPath = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = "$DesktopPath\PTT Dictation.lnk"
Write-Host "Creating Desktop shortcut..." -ForegroundColor Gray

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$TargetDir\run_tray.bat"
$Shortcut.WorkingDirectory = "$TargetDir"
$Shortcut.IconLocation = "C:\Windows\System32\mmres.dll,-3014"
$Shortcut.Description = "Push-to-Talk Local GPU Dictation"
$Shortcut.Save()

# Configure Desktop shortcut to Run as Administrator
$bytes = [System.IO.File]::ReadAllBytes($ShortcutPath)
$bytes[0x15] = $bytes[0x15] -bor 0x20
[System.IO.File]::WriteAllBytes($ShortcutPath, $bytes)

# 6. Create Startup Shortcut
$StartupPath = [Environment]::GetFolderPath('Startup')
$StartupShortcutPath = "$StartupPath\PTT Dictation.lnk"
Write-Host "Creating Startup shortcut (to run automatically on Windows login)..." -ForegroundColor Gray

$StartupShortcut = $WshShell.CreateShortcut($StartupShortcutPath)
$StartupShortcut.TargetPath = "$TargetDir\run_tray.bat"
$StartupShortcut.WorkingDirectory = "$TargetDir"
$StartupShortcut.IconLocation = "C:\Windows\System32\mmres.dll,-3014"
$StartupShortcut.Description = "Push-to-Talk Local GPU Dictation"
$StartupShortcut.Save()

# Configure Startup shortcut to Run as Administrator
$bytesStartup = [System.IO.File]::ReadAllBytes($StartupShortcutPath)
$bytesStartup[0x15] = $bytesStartup[0x15] -bor 0x20
[System.IO.File]::WriteAllBytes($StartupShortcutPath, $bytesStartup)

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "      Installation Completed Successfully!        " -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host "PTT Dictation has been installed to:" -ForegroundColor Gray
Write-Host "  $TargetDir" -ForegroundColor Green
Write-Host ""
Write-Host "Features Configured:" -ForegroundColor Gray
Write-Host "  [+] Desktop shortcut created (with Auto-Admin elevation)" -ForegroundColor Green
Write-Host "  [+] Startup folder shortcut created (to run at Windows login)" -ForegroundColor Green
Write-Host ""
Write-Host "Launching PTT Dictation now..." -ForegroundColor Cyan

# Launch the app from the new shortcut
Start-Process $ShortcutPath
