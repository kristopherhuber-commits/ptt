# install.ps1
# Installer script for Push-to-Talk Dictation
#
# LOAD-BEARING FIRST LINE: install.bat greps this file for the literal
# `# install.ps1` before it runs it, because a payload verifier cannot verify
# itself and an extraction that blanks this file leaves PowerShell reporting an
# empty command at line 1. Change the first line and that guard stops guarding.

$AppName = "PTT Dictation"
# This script lives in _internal\; the payload it installs is its parent.
# Moved there in v3.0 so the extracted folder offers the user exactly one
# executable -- with extensions hidden, which is the Windows default,
# `install.bat` and `install.ps1` both render as `install`.
$SourceDir = Split-Path $PSScriptRoot -Parent
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

# 1a. Verify the extraction before anything is copied.
#
# **The archive is not what arrives.** v3.0 was reported as a broken installer:
# `install.ps1` failed at line 1, character 1, with "The term '' is not
# recognized". The file was the right length and every byte in it was NUL --
# and so were 2,137 others, out of 8,550, all written by Windows Explorer's own
# "Extract All" from an archive that passed `unzip -t` without a complaint. The
# extractor does this silently on an archive this size, and every symptom it
# produces points somewhere else: at PowerShell, at the installer, at the
# package. Not one of them points at the extraction.
#
# So the package carries its own manifest and the installer checks it. Three
# seconds of SHA-256 (measured: 2.95 GiB at ~1 GB/s) buys the difference between
# an installation that fails later in a way nobody can read and one that stops
# here saying which files did not survive and what to run instead.
#
# A missing manifest is a warning rather than a refusal. It means an archive
# built before v3.0.1, or a tree somebody assembled by hand, and neither is a
# reason not to install.
$ManifestPath = Join-Path $PSScriptRoot "manifest.sha256"
$PackageVersion = ""
if (-not (Test-Path $ManifestPath)) {
    Write-Host "Note: this package carries no manifest, so the extraction cannot be verified." -ForegroundColor Yellow
    Write-Host ""
} else {
    Write-Host "Verifying the extracted files..." -ForegroundColor Gray
    $Expected = @{}
    foreach ($Line in [System.IO.File]::ReadAllLines($ManifestPath)) {
        $Line = $Line.Trim()
        if ($Line.Length -eq 0) { continue }
        if ($Line.StartsWith("#")) {
            if ($Line -match '^#\s*PTT Dictation\s+(\S+)') { $PackageVersion = $Matches[1] }
            continue
        }
        $Split = $Line.IndexOf("  ")
        if ($Split -lt 0) { continue }
        $Expected[$Line.Substring($Split + 2).Trim()] = $Line.Substring(0, $Split).Trim()
    }

    # Opened with FileShare.ReadWrite and hashed through one reused SHA-256
    # object: the alternative is Get-FileHash 8,550 times, which is the same
    # arithmetic wrapped in 8,550 cmdlet invocations and takes minutes.
    $Sha = [System.Security.Cryptography.SHA256]::Create()
    $Missing = New-Object System.Collections.Generic.List[string]
    $Corrupt = New-Object System.Collections.Generic.List[string]
    $Blank = 0
    foreach ($Relative in $Expected.Keys) {
        $Full = Join-Path $SourceDir ($Relative -replace '/', '\')
        if (-not [System.IO.File]::Exists($Full)) { $Missing.Add($Relative); continue }
        $Stream = [System.IO.File]::Open($Full, 'Open', 'Read', 'ReadWrite')
        try {
            $Actual = [System.BitConverter]::ToString($Sha.ComputeHash($Stream)).Replace("-", "").ToLowerInvariant()
        } finally { $Stream.Dispose() }
        if ($Actual -ne $Expected[$Relative]) {
            $Corrupt.Add($Relative)
            # Naming the symptom is what identifies the culprit. "Does not
            # match" could be anything; "the right length and entirely blank"
            # is the extractor, and only the extractor.
            if ((Get-Item $Full).Length -gt 0) {
                $Bytes = [System.IO.File]::ReadAllBytes($Full)
                $NonZero = $false
                foreach ($B in $Bytes) { if ($B -ne 0) { $NonZero = $true; break } }
                if (-not $NonZero) { $Blank++ }
            }
        }
    }

    if ($Missing.Count -gt 0 -or $Corrupt.Count -gt 0) {
        Write-Host ""
        Write-Host "==================================================" -ForegroundColor Red
        Write-Host "  This folder is not a complete copy of the app.  " -ForegroundColor Red
        Write-Host "==================================================" -ForegroundColor Red
        Write-Host ""
        if ($Missing.Count -gt 0) {
            Write-Host "  $($Missing.Count) file(s) are missing." -ForegroundColor Yellow
        }
        if ($Corrupt.Count -gt 0) {
            Write-Host "  $($Corrupt.Count) file(s) do not match the package." -ForegroundColor Yellow
            if ($Blank -gt 0) {
                Write-Host "  $Blank of those are the right size and entirely blank." -ForegroundColor Yellow
            }
        }
        Write-Host ""
        Write-Host "  The download is almost certainly fine. Windows Explorer's" -ForegroundColor Gray
        Write-Host "  'Extract All' loses files from an archive this large, and" -ForegroundColor Gray
        Write-Host "  gives no error when it does." -ForegroundColor Gray
        Write-Host ""
        Write-Host "  Extract it again with Windows' own tar, which does not." -ForegroundColor Gray
        Write-Host "  Open Command Prompt in the folder holding the .zip and run:" -ForegroundColor Gray
        Write-Host ""
        Write-Host "      rmdir /s /q ptt_dictate_dist" -ForegroundColor Cyan
        Write-Host "      mkdir ptt_dictate_dist" -ForegroundColor Cyan
        Write-Host "      tar -xf ptt_dictate_dist.zip -C ptt_dictate_dist" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  Then run install.bat from the new folder." -ForegroundColor Gray
        Write-Host ""
        Write-Host "  The first few files that did not survive:" -ForegroundColor Gray
        foreach ($Name in (@($Missing) + @($Corrupt) | Select-Object -First 8)) {
            Write-Host "    $Name" -ForegroundColor Gray
        }
        Write-Host ""
        Exit 1
    }
    Write-Host "  $($Expected.Count) files verified against the package manifest." -ForegroundColor Green
    if ($PackageVersion) {
        Write-Host "  Installing PTT Dictation $PackageVersion." -ForegroundColor Green
    }
    Write-Host ""
}

# 1b. Take the Mark of the Web off the payload.
#
# A file extracted from a downloaded archive inherits its `Zone.Identifier`
# stream, and `Copy-Item` carries that stream into the installation. It is worth
# removing for the same reason the verification above is worth doing: on a
# machine with Smart App Control on, an internet-marked script is refused
# outright -- which is how the same report that produced the manifest also
# arrived saying "Smart App Control blocked a file" with nothing naming which.
#
# Cheap enough not to need a reason: 0.7 seconds over 8,550 files. Extracting
# with `tar` avoids the mark entirely, but the installer cannot assume anybody
# read that part.
Write-Host "Clearing the downloaded-file mark..." -ForegroundColor Gray
Get-ChildItem -Path $SourceDir -Recurse -File -Force -ErrorAction SilentlyContinue |
    Unblock-File -ErrorAction SilentlyContinue

# 1c. The Concierge's runtime ships as a second archive, because one asset over
# 2 GiB is one GitHub will not accept (build_portable.py, DISTRIBUTION_ARCHIVE).
# Its absence is not an error: dictation never touches llama-server, and a user
# who does not want a local assistant is right to have skipped 628 MB. It is
# said out loud here because the alternative is finding out later, from a chat
# panel reporting a path that means nothing to anybody.
$ConciergeRuntime = "$SourceDir\app\llama\llama-server.exe"
if (-not (Test-Path $ConciergeRuntime)) {
    Write-Host ""
    Write-Host "Note: the Concierge runtime is not in this folder." -ForegroundColor Yellow
    Write-Host "  Dictation will work normally. The Concierge -- the local assistant that" -ForegroundColor Gray
    Write-Host "  explains and changes your settings -- will not start without it." -ForegroundColor Gray
    Write-Host "  To add it: download ptt_llama_runtime.zip from the same release and" -ForegroundColor Gray
    Write-Host "  extract it into this folder, so that app\llama\ exists beside app\ptt\." -ForegroundColor Gray
    Write-Host "  Then run this installer again." -ForegroundColor Gray
    Write-Host ""
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
New-Item -ItemType Directory -Path "$TargetDir\_internal" -Force | Out-Null
Copy-Item -Path "$SourceDir\_internal\run_tray.bat" -Destination "$TargetDir\_internal\run_tray.bat" -Force

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
$Shortcut.TargetPath = "$TargetDir\_internal\run_tray.bat"
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
$StartupShortcut.TargetPath = "$TargetDir\_internal\run_tray.bat"
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
