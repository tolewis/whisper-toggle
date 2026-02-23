# build.ps1 — Build dictate-toggle.exe with PyInstaller
# Run from the repo root: powershell -ExecutionPolicy Bypass -File windows\build.ps1
#
# Prerequisites:
#   pip install pyinstaller keyboard sounddevice soundfile numpy requests pyperclip winotify

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$OutDir = "$RepoRoot\dist"
$BuildDir = "$RepoRoot\build"

Write-Host ""
Write-Host "=== Building dictate-toggle.exe ===" -ForegroundColor Cyan
Write-Host ""

# Check PyInstaller
$pyi = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyi) {
    Write-Host "PyInstaller not found. Installing..." -ForegroundColor Yellow
    pip install pyinstaller
}

# Find sounddevice portaudio DLL (needed as hidden data)
$sdPath = python -c "import sounddevice; import os; print(os.path.dirname(sounddevice.__file__))" 2>$null
$portaudioDll = Get-ChildItem "$sdPath\_sounddevice_data\portaudio-binaries\*.dll" -ErrorAction SilentlyContinue | Select-Object -First 1

$extraArgs = @()
if ($portaudioDll) {
    Write-Host "  Found PortAudio: $($portaudioDll.FullName)"
    $extraArgs += "--add-binary"
    $extraArgs += "$($portaudioDll.FullName);_sounddevice_data/portaudio-binaries"
}

# Find soundfile DLL
$sfPath = python -c "import soundfile; import os; print(os.path.dirname(soundfile.__file__))" 2>$null
$sndfileDll = Get-ChildItem "$sfPath\_soundfile_data\*.dll" -ErrorAction SilentlyContinue | Select-Object -First 1

if ($sndfileDll) {
    Write-Host "  Found libsndfile: $($sndfileDll.FullName)"
    $extraArgs += "--add-binary"
    $extraArgs += "$($sndfileDll.FullName);_soundfile_data"
}

Write-Host "  Building..." -ForegroundColor Yellow
Write-Host ""

pyinstaller `
    --onefile `
    --name "dictate-toggle" `
    --console `
    --icon "$RepoRoot\windows\icon.ico" `
    --noconfirm `
    --clean `
    --hidden-import "winotify" `
    --hidden-import "sounddevice" `
    --hidden-import "soundfile" `
    --hidden-import "_sounddevice_data" `
    --hidden-import "_soundfile_data" `
    @extraArgs `
    "$RepoRoot\windows\dictate-toggle.py"

if (Test-Path "$OutDir\dictate-toggle.exe") {
    $size = [math]::Round((Get-Item "$OutDir\dictate-toggle.exe").Length / 1MB, 1)
    Write-Host ""
    Write-Host "=== Build complete ===" -ForegroundColor Green
    Write-Host "  Output: $OutDir\dictate-toggle.exe ($size MB)"
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "=== Build FAILED ===" -ForegroundColor Red
    exit 1
}

# Clean build artifacts
Remove-Item "$BuildDir" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$RepoRoot\dictate-toggle.spec" -Force -ErrorAction SilentlyContinue
