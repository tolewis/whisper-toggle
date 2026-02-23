# install.ps1 — Set up Whisper Toggle on Windows
# Run: powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

$VenvPath = "$env:LOCALAPPDATA\whisper-venv"
$AppDir = "$env:LOCALAPPDATA\whisper-toggle"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "=== Whisper Toggle — Windows Installer ===" -ForegroundColor Cyan
Write-Host ""

# ── Check Python ────────────────────────────────────────────────────────────
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "ERROR: Python not found. Install Python 3.9+ from python.org" -ForegroundColor Red
    exit 1
}
$pyVer = python --version 2>&1
Write-Host "  Python: $pyVer"

# ── Create venv ─────────────────────────────────────────────────────────────
if (Test-Path "$VenvPath\Scripts\python.exe") {
    Write-Host "  Venv: already exists at $VenvPath"
} else {
    Write-Host "  Creating venv at $VenvPath ..."
    python -m venv $VenvPath
}

$pip = "$VenvPath\Scripts\pip"
$vpython = "$VenvPath\Scripts\python"

# ── Install API dependencies ───────────────────────────────────────────────
Write-Host ""
Write-Host "Installing API dependencies..." -ForegroundColor Yellow
& $pip install --quiet faster-whisper fastapi uvicorn

# ── Install CUDA PyTorch (if NVIDIA GPU present) ──────────────────────────
$nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvidiaSmi) {
    Write-Host "  NVIDIA GPU detected — installing CUDA PyTorch..."
    & $pip install --quiet torch --index-url https://download.pytorch.org/whl/cu121
} else {
    Write-Host "  No NVIDIA GPU detected — using CPU mode"
    Write-Host "  (Set WHISPER_API_DEVICE=cpu in start-api.bat)"
}

# ── Install dictation dependencies ─────────────────────────────────────────
Write-Host ""
Write-Host "Installing dictation dependencies..." -ForegroundColor Yellow
& $pip install --quiet -r "$RepoRoot\windows\requirements.txt"

# ── Deploy files ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Deploying to $AppDir ..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null

Copy-Item "$RepoRoot\app.py" "$AppDir\" -Force
Copy-Item "$RepoRoot\windows\dictate-toggle.py" "$AppDir\" -Force
Copy-Item "$RepoRoot\windows\start-api.bat" "$AppDir\" -Force

# ── Summary ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Installation complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Files installed to: $AppDir"
Write-Host ""
Write-Host "Step 1 — Start the API server (keep this terminal open):"
Write-Host "  cd $AppDir" -ForegroundColor White
Write-Host "  .\start-api.bat" -ForegroundColor White
Write-Host ""
Write-Host "Step 2 — Start dictation (in a second terminal):"
Write-Host "  & '$vpython' '$AppDir\dictate-toggle.py'" -ForegroundColor White
Write-Host ""
Write-Host "Step 3 — Press Ctrl+`$('`') to toggle recording"
Write-Host ""
