# build-installer.ps1 — Build the Whisper Toggle Windows installer
#
# What this does:
#   1. Downloads a portable Python (no install required)
#   2. Installs all dependencies into it
#   3. Bundles everything into a single installer .exe via Inno Setup
#
# Prerequisites:
#   - Internet connection (downloads ~200MB of Python + packages)
#   - Inno Setup 6+ installed (https://jrsoftware.org/isinfo.php)
#     OR just run steps 1-2 for a portable folder without the installer
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File windows\build-installer.ps1
#

$ErrorActionPreference = "Stop"

# ── Config ──────────────────────────────────────────────────────────────────
$PythonVersion = "3.11"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BuildDir = "$RepoRoot\build"
$PythonDir = "$BuildDir\python"
$StageDir = "$BuildDir\stage"      # What goes into the installer
$OutDir = "$RepoRoot\dist"

# ── Banner ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║   Whisper Toggle — Installer Builder          ║" -ForegroundColor Cyan
Write-Host "  ╚═══════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Get portable Python ────────────────────────────────────────────
if (Test-Path "$PythonDir\python.exe") {
    Write-Host "  [1/5] Python already downloaded — skipping" -ForegroundColor Green
} else {
    Write-Host "  [1/5] Downloading portable Python $PythonVersion ..." -ForegroundColor Yellow

    # Query GitHub for latest python-build-standalone release
    $headers = @{}
    if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }

    $releasesUrl = "https://api.github.com/repos/indygreg/python-build-standalone/releases?per_page=10"
    $releases = Invoke-RestMethod -Uri $releasesUrl -Headers $headers

    $asset = $null
    foreach ($release in $releases) {
        $asset = $release.assets | Where-Object {
            $_.name -match "cpython-3\.11\.\d+\+\d+-x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz$"
        } | Select-Object -First 1
        if ($asset) { break }
    }

    if (-not $asset) {
        Write-Host "  ERROR: Could not find Python $PythonVersion build." -ForegroundColor Red
        Write-Host "  Check https://github.com/indygreg/python-build-standalone/releases"
        exit 1
    }

    Write-Host "  Found: $($asset.name)"
    $archivePath = "$BuildDir\python-standalone.tar.gz"
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

    Write-Host "  Downloading ($([math]::Round($asset.size / 1MB, 1)) MB)..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archivePath

    Write-Host "  Extracting..."
    tar -xzf $archivePath -C $BuildDir

    # python-build-standalone extracts to a "python" folder
    if (-not (Test-Path "$PythonDir\python.exe")) {
        # Some builds extract to "python/install" or similar — find it
        $found = Get-ChildItem -Path $BuildDir -Recurse -Filter "python.exe" |
            Where-Object { $_.Directory.Name -ne "Scripts" } |
            Select-Object -First 1
        if ($found) {
            $extractedDir = $found.Directory.FullName
            if ($extractedDir -ne $PythonDir) {
                Move-Item $extractedDir $PythonDir -Force
            }
        } else {
            Write-Host "  ERROR: python.exe not found after extraction" -ForegroundColor Red
            exit 1
        }
    }

    Remove-Item $archivePath -Force -ErrorAction SilentlyContinue
    Write-Host "  Python ready at $PythonDir" -ForegroundColor Green
}

$pip = "$PythonDir\python.exe -m pip"

# ── Step 2: Install dependencies ───────────────────────────────────────────
Write-Host ""
Write-Host "  [2/5] Installing dependencies..." -ForegroundColor Yellow

# Upgrade pip first
& "$PythonDir\python.exe" -m pip install --upgrade pip --quiet 2>$null

# API server deps
Write-Host "    API server: faster-whisper, fastapi, uvicorn"
& "$PythonDir\python.exe" -m pip install --quiet `
    faster-whisper fastapi uvicorn

# Tray app deps
Write-Host "    Tray app: keyboard, sounddevice, soundfile, pystray, etc."
& "$PythonDir\python.exe" -m pip install --quiet `
    keyboard sounddevice soundfile numpy requests pyperclip pystray Pillow

Write-Host "  Dependencies installed" -ForegroundColor Green

# ── Step 3: Stage files ────────────────────────────────────────────────────
Write-Host ""
Write-Host "  [3/5] Staging files..." -ForegroundColor Yellow

if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

# Copy embedded Python (with all installed packages)
Copy-Item $PythonDir "$StageDir\python" -Recurse

# Copy application files
Copy-Item "$RepoRoot\app.py" "$StageDir\"
Copy-Item "$RepoRoot\windows\whisper-toggle-tray.pyw" "$StageDir\"

Write-Host "  Staged to $StageDir" -ForegroundColor Green

# Calculate total size
$totalSize = [math]::Round(
    (Get-ChildItem $StageDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 0
)
Write-Host "  Total bundle size: ~${totalSize} MB"

# ── Step 4: Compile installer (if Inno Setup available) ────────────────────
Write-Host ""

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "ISCC.exe"
) | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1

if ($iscc) {
    Write-Host "  [4/5] Compiling installer with Inno Setup..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

    & $iscc /O"$OutDir" "$RepoRoot\windows\installer.iss"

    if ($LASTEXITCODE -eq 0) {
        $installer = Get-ChildItem "$OutDir\WhisperToggle-*.exe" | Select-Object -First 1
        $installerSize = [math]::Round($installer.Length / 1MB, 1)
        Write-Host ""
        Write-Host "  [5/5] Done!" -ForegroundColor Green
        Write-Host ""
        Write-Host "  ╔═══════════════════════════════════════════════╗" -ForegroundColor Green
        Write-Host "  ║   Installer: $($installer.Name) ($installerSize MB)" -ForegroundColor Green
        Write-Host "  ║   Location:  $OutDir\" -ForegroundColor Green
        Write-Host "  ╚═══════════════════════════════════════════════╝" -ForegroundColor Green
    } else {
        Write-Host "  Inno Setup compilation failed (exit code $LASTEXITCODE)" -ForegroundColor Red
    }
} else {
    Write-Host "  [4/5] Inno Setup not found — skipping installer compilation" -ForegroundColor Yellow
    Write-Host "        Install from: https://jrsoftware.org/isinfo.php" -ForegroundColor DarkGray
    Write-Host "        Then re-run this script to build the .exe installer" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  [5/5] Portable build ready" -ForegroundColor Green
    Write-Host ""
    Write-Host "  You can run Whisper Toggle directly from the staged folder:"
    Write-Host "    $StageDir\python\pythonw.exe $StageDir\whisper-toggle-tray.pyw" -ForegroundColor White
}

Write-Host ""
