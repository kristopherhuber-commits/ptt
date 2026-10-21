# uninstall.ps1
# Uninstaller for Push-to-Talk Dictation
#
# Two ways in, one script. The Advanced tab's "Uninstall PTT Dictation..."
# button runs it with -Confirmed, having already asked; Settings > Apps >
# Installed apps runs it through the UninstallString install.ps1 writes under
# HKCU, and it asks here. Windows itself removes nothing -- the registry entry
# is only a name and this command line -- so this file is the whole list of
# what PTT puts on a machine:
#
#   the installation      %LOCALAPPDATA%\Programs\ptt_dictate -- the app, its
#                         Python, config.json, the Concierge weights in
#                         app\models, and this script
#   the Whisper models    faster-whisper's downloads in the Hugging Face cache,
#                         outside the installation
#   two shortcuts         Desktop and Startup, both "PTT Dictation.lnk"
#   the registry entry    the Installed apps listing itself
#
# Deliberately kept: ~\agent-configs\vocabulary.json. It belongs to the
# agent-configs repository, not to PTT, and is the one copy of the vocabulary
# rules that outlives this.

param(
    # The Advanced tab has already asked; Settings > Apps has not.
    [switch]$Confirmed,
    # Overridable so the script can be run against a scratch tree. A run with
    # any of them overridden touches nothing else and does not elevate.
    [string]$TargetDir = "$env:LOCALAPPDATA\Programs\ptt_dictate",
    [string]$HubDir = "",
    [string]$DesktopDir = [Environment]::GetFolderPath('Desktop'),
    [string]$StartupDir = [Environment]::GetFolderPath('Startup'),
    [string]$RegistryKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PTTDictation"
)

$AppName = "PTT Dictation"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# This script lives inside the folder it deletes, and a process whose working
# directory is in a folder keeps that folder from being removed.
Set-Location $env:TEMP

# The app runs elevated (the shortcut's run-as-admin flag), and only an
# elevated process can stop it. The Advanced tab's launch inherits elevation;
# Settings > Apps does not, so ask for it here -- the same prompt install.bat
# raises.
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$Scratch = $PSBoundParameters.Keys | Where-Object { $_ -ne "Confirmed" }
if (-not $IsAdmin -and -not $Scratch) {
    $Arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Confirmed) { $Arguments += "-Confirmed" }
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList $Arguments
    } catch {
        Write-Host "Uninstall cancelled: administrator permission is needed to stop PTT Dictation." -ForegroundColor Yellow
        Read-Host "Press Enter to close"
    }
    Exit
}

if (-not $HubDir) {
    # huggingface_hub's own resolution order, so this finds what faster-whisper
    # downloaded wherever the user pointed it.
    if ($env:HF_HUB_CACHE) { $HubDir = $env:HF_HUB_CACHE }
    elseif ($env:HF_HOME) { $HubDir = Join-Path $env:HF_HOME "hub" }
    else { $HubDir = Join-Path $env:USERPROFILE ".cache\huggingface\hub" }
}

# Only faster-whisper's repositories. The cache is shared with every other tool
# that downloads from Hugging Face, so the folder itself is not PTT's to delete.
$WhisperModels = @()
if (Test-Path $HubDir) {
    $WhisperModels = @(Get-ChildItem -Path $HubDir -Directory -Filter "models--*--faster-whisper-*" -ErrorAction SilentlyContinue)
}
$Shortcuts = @("$DesktopDir\PTT Dictation.lnk", "$StartupDir\PTT Dictation.lnk")

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "          $AppName Uninstaller                    " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This removes:" -ForegroundColor Gray
Write-Host "  $TargetDir" -ForegroundColor Gray
Write-Host "    (the app, your settings and the Concierge's downloaded model)" -ForegroundColor Gray
foreach ($Model in $WhisperModels) {
    Write-Host "  $($Model.FullName)" -ForegroundColor Gray
}
Write-Host "  the Desktop and Startup shortcuts" -ForegroundColor Gray
Write-Host ""
Write-Host "It keeps ~\agent-configs\vocabulary.json, if you have one." -ForegroundColor Gray
Write-Host ""

if (-not $Confirmed) {
    $Answer = Read-Host "Type Y and press Enter to uninstall"
    if ($Answer -notmatch '^\s*y(es)?\s*$') {
        Write-Host "Nothing was changed." -ForegroundColor Yellow
        Read-Host "Press Enter to close"
        Exit
    }
}

# 1. Stop PTT. From the Advanced tab the app is already quitting, so give it a
# moment to go on its own before forcing it. Matched by path, not by name: the
# installer's Stop-Process -Name pythonw would also stop any other Python.
function Get-PttProcesses {
    $Prefix = $TargetDir.TrimEnd('\') + '\'
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase) })
}
# Every call wrapped in @(): a function's output is unrolled, and on Windows
# PowerShell 5.1 a lone CimInstance has no .Count, so one running process
# counted as none.
if (@(Get-PttProcesses).Count -gt 0) {
    Write-Host "Waiting for PTT Dictation to close..." -ForegroundColor Gray
    for ($i = 0; $i -lt 10 -and @(Get-PttProcesses).Count -gt 0; $i++) { Start-Sleep -Seconds 1 }
    foreach ($Process in @(Get-PttProcesses)) {
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

# 2. The shortcuts.
foreach ($Shortcut in $Shortcuts) {
    if (Test-Path $Shortcut) {
        Write-Host "Removing $Shortcut..." -ForegroundColor Gray
        Remove-Item -Path $Shortcut -Force -ErrorAction SilentlyContinue
    }
}

# 3. The Whisper models, and their lock folders beside them.
foreach ($Model in $WhisperModels) {
    Write-Host "Removing Whisper model $($Model.Name)..." -ForegroundColor Gray
    Remove-Item -Path $Model.FullName -Recurse -Force -ErrorAction SilentlyContinue
    $Lock = Join-Path $HubDir ".locks\$($Model.Name)"
    if (Test-Path $Lock) { Remove-Item -Path $Lock -Recurse -Force -ErrorAction SilentlyContinue }
}

# 4. The installation. This script goes last, so a removal that stops partway
# leaves the Installed apps entry pointing at a script that still exists.
# Retried, because a process that has just been stopped can hold its files for
# a moment after it is gone from the process list.
function Remove-Installation {
    Get-ChildItem -LiteralPath $TargetDir -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "_internal" } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    $Internal = Join-Path $TargetDir "_internal"
    if (Test-Path $Internal) {
        Get-ChildItem -LiteralPath $Internal -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne "uninstall.ps1" } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
    $Rest = @(Get-ChildItem -LiteralPath $TargetDir -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "uninstall.ps1" })
    if ($Rest.Count -eq 0) {
        Remove-Item -LiteralPath $TargetDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
if (Test-Path $TargetDir) {
    Write-Host "Removing $TargetDir (this may take a minute)..." -ForegroundColor Gray
    for ($i = 0; $i -lt 5 -and (Test-Path $TargetDir); $i++) {
        Remove-Installation
        if (Test-Path $TargetDir) { Start-Sleep -Seconds 2 }
    }
}

# 5. The Installed apps entry -- last, and only once the folder is gone, so a
# removal that failed can be retried from the same place it was started.
$Left = @($Shortcuts | Where-Object { Test-Path $_ }) +
        @($WhisperModels | Where-Object { Test-Path $_.FullName } | ForEach-Object { $_.FullName })
if (Test-Path $TargetDir) { $Left = @($TargetDir) + $Left }

Write-Host ""
if ($Left.Count -eq 0) {
    if (Test-Path $RegistryKey) { Remove-Item -Path $RegistryKey -Recurse -Force -ErrorAction SilentlyContinue }
    Write-Host "==================================================" -ForegroundColor Green
    Write-Host "      $AppName has been uninstalled.              " -ForegroundColor Green
    Write-Host "==================================================" -ForegroundColor Green
} else {
    Write-Host "==================================================" -ForegroundColor Red
    Write-Host "  Some files could not be removed.                " -ForegroundColor Red
    Write-Host "==================================================" -ForegroundColor Red
    Write-Host ""
    foreach ($Path in $Left) { Write-Host "  $Path" -ForegroundColor Yellow }
    Write-Host ""
    Write-Host "  Something still has them open. Restart Windows, then uninstall" -ForegroundColor Gray
    Write-Host "  again from Settings > Apps > Installed apps." -ForegroundColor Gray
}
Write-Host ""
Read-Host "Press Enter to close"
