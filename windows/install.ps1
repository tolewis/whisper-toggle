# install.ps1 -- Developer install for Whisper Toggle on Windows
# Preferred end-user path: build/run windows\build-installer.ps1 and use the Inno installer.
# Run from repo root or windows dir:
#   powershell -ExecutionPolicy Bypass -File windows\install.ps1

$ErrorActionPreference = "Stop"

$VenvPath = "$env:LOCALAPPDATA\Whisper Toggle\venv"
$AppDir = "$env:LOCALAPPDATA\Whisper Toggle"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "=== Whisper Toggle -- Developer Install ===" -ForegroundColor Cyan
Write-Host ""

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "ERROR: Python not found. Install Python 3.11+ from python.org" -ForegroundColor Red
    exit 1
}

Write-Host "Python: $(python --version 2>&1)"

if (Test-Path "$VenvPath\Scripts\python.exe") {
    Write-Host "Venv: already exists at $VenvPath" -ForegroundColor Green
} else {
    Write-Host "Creating venv at $VenvPath ..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $VenvPath) | Out-Null
    python -m venv $VenvPath
}

$pip = "$VenvPath\Scripts\pip.exe"
$vpython = "$VenvPath\Scripts\python.exe"
$vpythonw = "$VenvPath\Scripts\pythonw.exe"

Write-Host "Installing runtime dependencies..." -ForegroundColor Yellow
& $vpython -m pip install --upgrade pip --quiet
& $pip install --quiet -r "$RepoRoot\requirements.txt"
& $pip install --quiet -r "$RepoRoot\windows\requirements.txt"

Write-Host "Deploying app files to $AppDir ..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
Copy-Item "$RepoRoot\app.py" "$AppDir\" -Force
Copy-Item "$RepoRoot\windows\whisper-toggle-tray.pyw" "$AppDir\" -Force
Copy-Item "$RepoRoot\windows\tray_app.py" "$AppDir\" -Force
Copy-Item "$RepoRoot\windows\settings_gui.py" "$AppDir\" -Force
Copy-Item "$RepoRoot\windows\disable-win-voice-typing.ps1" "$AppDir\" -Force
Copy-Item "$RepoRoot\whisper_toggle" "$AppDir\whisper_toggle" -Recurse -Force
Copy-Item "$RepoRoot\assets" "$AppDir\assets" -Recurse -Force
if (Test-Path "$RepoRoot\vendor\whisper_streaming") {
    New-Item -ItemType Directory -Force -Path "$AppDir\vendor" | Out-Null
    Copy-Item "$RepoRoot\vendor\whisper_streaming" "$AppDir\vendor\whisper_streaming" -Recurse -Force
}

Write-Host ""
Write-Host "=== Installation complete ===" -ForegroundColor Green
Write-Host "Files installed to: $AppDir"
Write-Host "Launch tray app:" -ForegroundColor White
Write-Host "  & '$vpythonw' '$AppDir\whisper-toggle-tray.pyw'"
Write-Host "Default hotkey: Ctrl+Shift+H"
