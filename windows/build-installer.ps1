# build-installer.ps1 -- Build Whisper Toggle 2.0 Windows installer
#
# 1. Downloads portable Python
# 2. Installs API + tray deps
# 3. Stages app files
# 4. Compiles Inno Setup installer when ISCC is available
#
# Usage (on jubiku):
#   powershell -ExecutionPolicy Bypass -File windows\build-installer.ps1

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BuildDir = "$RepoRoot\build"
$PythonDir = "$BuildDir\python"
$StageDir = "$BuildDir\stage"
$OutDir = "$RepoRoot\dist"

Write-Host ""
Write-Host "  =================================================" -ForegroundColor Cyan
Write-Host "    Whisper Toggle 2.0 -- Installer Builder" -ForegroundColor Cyan
Write-Host "  =================================================" -ForegroundColor Cyan
Write-Host ""

# -- Step 1: portable Python --
if (Test-Path "$PythonDir\python.exe") {
    Write-Host "  [1/5] Python already downloaded -- skipping" -ForegroundColor Green
} else {
    Write-Host "  [1/5] Downloading portable Python 3.11 ..." -ForegroundColor Yellow
    $headers = @{}
    if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
    $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/indygreg/python-build-standalone/releases?per_page=10" -Headers $headers
    $asset = $null
    foreach ($release in $releases) {
        $asset = $release.assets | Where-Object {
            $_.name -match 'cpython-3\.11\.\d+\+\d+-x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz$'
        } | Select-Object -First 1
        if ($asset) { break }
    }
    if (-not $asset) { throw "Could not find Python 3.11 standalone build" }
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    $archivePath = "$BuildDir\python-standalone.tar.gz"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archivePath
    tar -xzf $archivePath -C $BuildDir
    if (-not (Test-Path "$PythonDir\python.exe")) {
        $found = Get-ChildItem -Path $BuildDir -Recurse -Filter "python.exe" |
            Where-Object { $_.Directory.Name -ne "Scripts" } | Select-Object -First 1
        if (-not $found) { throw "python.exe not found after extraction" }
        if ($found.Directory.FullName -ne $PythonDir) {
            Move-Item $found.Directory.FullName $PythonDir -Force
        }
    }
    Remove-Item $archivePath -Force -ErrorAction SilentlyContinue
    Write-Host "  Python ready at $PythonDir" -ForegroundColor Green
}

# -- Step 2: deps --
Write-Host ""
Write-Host "  [2/5] Installing dependencies..." -ForegroundColor Yellow
& "$PythonDir\python.exe" -m pip install --upgrade pip --quiet 2>$null
Write-Host "    API: faster-whisper, fastapi, uvicorn, websockets, numpy"
& "$PythonDir\python.exe" -m pip install --quiet `
    "faster-whisper==1.2.1" "ctranslate2==4.7.1" "fastapi==0.131.0" "uvicorn[standard]==0.41.0" `
    "python-multipart>=0.0.20" "numpy>=1.26" "websockets>=15.0" "httpx>=0.27"
Write-Host "    Tray: keyboard, sounddevice, pystray, Pillow, ..."
& "$PythonDir\python.exe" -m pip install --quiet `
    keyboard sounddevice soundfile numpy requests pyperclip pystray Pillow winotify
Write-Host "  Dependencies installed" -ForegroundColor Green

# -- Step 3: stage --
Write-Host ""
Write-Host "  [3/5] Staging files..." -ForegroundColor Yellow
if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
Copy-Item $PythonDir "$StageDir\python" -Recurse
Copy-Item "$RepoRoot\app.py" "$StageDir\"
Copy-Item "$RepoRoot\windows\whisper-toggle-tray.pyw" "$StageDir\"
Copy-Item "$RepoRoot\windows\tray_app.py" "$StageDir\"
Copy-Item "$RepoRoot\windows\settings_gui.py" "$StageDir\"
Copy-Item "$RepoRoot\windows\disable-win-voice-typing.ps1" "$StageDir\"
Copy-Item "$RepoRoot\whisper_toggle" "$StageDir\whisper_toggle" -Recurse
# Streaming vendor (ufal whisper_streaming pin)
if (Test-Path "$RepoRoot\vendor\whisper_streaming") {
    New-Item -ItemType Directory -Force -Path "$StageDir\vendor" | Out-Null
    Copy-Item "$RepoRoot\vendor\whisper_streaming" "$StageDir\vendor\whisper_streaming" -Recurse
}
# Drop pycache from stage
Get-ChildItem "$StageDir" -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item "$RepoRoot\assets" "$StageDir\assets" -Recurse
# Ensure icon exists
if (-not (Test-Path "$StageDir\assets\icon.ico")) {
    & "$PythonDir\python.exe" -c "from pathlib import Path; from whisper_toggle.icons import write_app_icon; write_app_icon(Path(r'$StageDir\assets\icon.ico'))"
}
Write-Host "  Staged to $StageDir" -ForegroundColor Green
$totalBytes = (Get-ChildItem $StageDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host "  Total bundle size: ~$([math]::Round($totalBytes / 1MB, 0)) MB"

# -- Step 4: Inno Setup --
Write-Host ""
$isccPaths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $null
foreach ($p in $isccPaths) { if (Test-Path $p) { $iscc = $p; break } }

if ($iscc) {
    Write-Host "  [4/5] Compiling installer with Inno Setup..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    & $iscc /O"$OutDir" "$RepoRoot\windows\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed: $LASTEXITCODE" }
    $installer = Get-ChildItem "$OutDir\WhisperToggle-*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    Write-Host ""
    Write-Host "  [5/5] Done!" -ForegroundColor Green
    Write-Host "  Installer: $($installer.FullName) ($([math]::Round($installer.Length/1MB,1)) MB)" -ForegroundColor Green
} else {
    Write-Host "  [4/5] Inno Setup not found -- portable stage only" -ForegroundColor Yellow
    Write-Host "  [5/5] Run: $StageDir\python\pythonw.exe $StageDir\whisper-toggle-tray.pyw"
}

Write-Host ""
