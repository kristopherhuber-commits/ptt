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
#
# Two things in the old installation must survive it, and neither did before
# (concierge_design.md section 10, Q27):
#
#   app\models\   -- the downloaded Concierge weights, about 6.9 GB over a link
#                    the user has already paid for once, plus any bundled
#                    Whisper model. Deleting these turns an upgrade into a
#                    6.9 GB re-download.
#   app\config.json -- every saved setting: hotkey, microphone, model,
#                    vocabulary rules, and the Concierge opt-in state. This one
#                    is an existing v2 defect. A reinstall has always silently
#                    reset the user's settings; v3 makes the same bug expensive
#                    rather than merely annoying.
#
# They are moved *aside* rather than copied, so a 6.9 GB file is a rename on the
# same volume and not a second 6.9 GB write. Moved back after the copy, and a
# file is only put back if the new payload did not bring one -- which it never
# should, because build_portable.py excludes every name below.
#
# **The file list, and why it is a list (session 3).** Q27 named two things
# because two things existed when it was written. The Concierge has since added
# durable state of its own -- the memory note it keeps about the user, the one
# kept previous version of it, and the transcripts the user chose to save -- and
# a reinstall deleted all three while carefully preserving 6.9 GB of weights.
# They are per-machine files the user cannot recreate, which is the test
# config.json already passes, so they join the same list rather than becoming
# three more variables.
#
# What is deliberately *absent* is the rest of build_portable.py's
# RUNTIME_ARTIFACTS: both debug logs rotate at every start (OBS-4), and
# concierge_state.json and concierge_key describe one launch of one process.
# Carrying those across a reinstall would preserve a stale pid and a dead key.
$PreserveDir = "$env:TEMP\ptt_dictate_preserve"
$PreservedModels = $false
$PreservedFiles = @(
    "config.json",
    "concierge_memory.txt",
    "concierge_memory.prev.txt",
    "concierge_sessions.json"
)
$PreservedNames = @()

# The other half of RUNTIME_ARTIFACTS: per-launch files that are neither
# preserved nor shipped, and which the copy above can nevertheless carry into a
# fresh installation.
#
# build_portable.py keeps all of these out of the *archive*. It cannot keep them
# out of the *source directory*, and Copy-Item takes app\ wholesale -- so a user
# who extracts the zip, runs the application once and then runs install.bat
# installs that run's API key, that run's log, and that run's
# concierge_state.json, which names a pid and a port belonging to a process that
# has already exited. The startup reap is defended against exactly that (pid +
# create time + image name + /props alias, server.py), so the consequence is
# contained; it is removed here because "never ships" should be true of both
# paths and not only of the one that was checked.
$DisposableFiles = @(
    "debug_log.txt",
    "debug_log.prev.txt",
    "concierge_state.json",
    "concierge_key"
)

if (Test-Path $TargetDir) {
    Write-Host "An existing installation was found. Attempting to close active instances..." -ForegroundColor Yellow
    # Try to close any running instances
    Stop-Process -Name "ptt_dictate" -Force -ErrorAction SilentlyContinue
    Stop-Process -Name "pythonw" -Force -ErrorAction SilentlyContinue
    # The Concierge's llama-server is killed by its job object when the app dies,
    # including under this Stop-Process. Named here so nobody adds a second kill.
    Start-Sleep -Seconds 2

    if (Test-Path $PreserveDir) { Remove-Item -Path $PreserveDir -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $PreserveDir -Force | Out-Null

    if (Test-Path "$TargetDir\app\models") {
        Write-Host "Setting aside downloaded models (they are not re-downloaded)..." -ForegroundColor Gray
        try {
            Move-Item -Path "$TargetDir\app\models" -Destination "$PreserveDir\models" -Force -ErrorAction Stop
            $PreservedModels = $true
        } catch {
            Write-Host "Warning: could not set aside app\models; it may be re-downloaded." -ForegroundColor Yellow
        }
    }
    foreach ($Name in $PreservedFiles) {
        if (Test-Path "$TargetDir\app\$Name") {
            Write-Host "Setting aside $Name..." -ForegroundColor Gray
            try {
                Move-Item -Path "$TargetDir\app\$Name" -Destination "$PreserveDir\$Name" -Force -ErrorAction Stop
                $PreservedNames += $Name
            } catch {
                Write-Host "Warning: could not set aside $Name; it will be lost." -ForegroundColor Yellow
            }
        }
    }

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

# 4a. Put back what was set aside in step 3.
if ($PreservedModels) {
    Write-Host "Restoring downloaded models..." -ForegroundColor Gray
    if (Test-Path "$TargetDir\app\models") { Remove-Item -Path "$TargetDir\app\models" -Recurse -Force -ErrorAction SilentlyContinue }
    Move-Item -Path "$PreserveDir\models" -Destination "$TargetDir\app\models" -Force
}
foreach ($Name in $PreservedNames) {
    if (Test-Path "$TargetDir\app\$Name") {
        # The archive should never contain one -- build_portable.py excludes
        # every name in $PreservedFiles as a per-machine runtime artifact. If
        # one is here anyway, the user's own file still wins.
        Write-Host "Warning: the package contained a $Name; keeping yours." -ForegroundColor Yellow
        Remove-Item -Path "$TargetDir\app\$Name" -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Restoring $Name..." -ForegroundColor Gray
    Move-Item -Path "$PreserveDir\$Name" -Destination "$TargetDir\app\$Name" -Force
}
if (Test-Path $PreserveDir) { Remove-Item -Path $PreserveDir -Recurse -Force -ErrorAction SilentlyContinue }

# 4b. Drop anything per-launch the source directory happened to be carrying.
foreach ($Name in $DisposableFiles) {
    if (Test-Path "$TargetDir\app\$Name") {
        Write-Host "Discarding $Name from the package (per-launch state)..." -ForegroundColor Gray
        Remove-Item -Path "$TargetDir\app\$Name" -Force -ErrorAction SilentlyContinue
    }
}

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
