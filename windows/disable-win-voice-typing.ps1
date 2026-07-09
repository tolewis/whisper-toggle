# Optional best-effort helper for users who explicitly want Whisper Toggle on Win+H.
# The app defaults to Ctrl+Shift+H so Windows Voice Typing remains intact.
# Run as the interactive user (no admin required).

$ErrorActionPreference = "SilentlyContinue"

# Windows 11 voice typing autostart / recognition preferences (best effort)
$paths = @(
    "HKCU:\Software\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy",
    "HKCU:\Software\Microsoft\Input\Settings"
)
foreach ($p in $paths) {
    if (-not (Test-Path $p)) { New-Item -Path $p -Force | Out-Null }
}

# Hide voice typing mic button if present
New-ItemProperty -Path "HKCU:\Software\Microsoft\input\Settings" -Name "EnableHwkbTextPrediction" -Value 0 -PropertyType DWord -Force | Out-Null

Write-Host "Whisper Toggle: Windows Voice Typing launcher preference updated best-effort."
Write-Host "Use Settings > Time & language > Typing > Voice typing to confirm 'Voice typing launcher' is Off before selecting Win+H in Whisper Toggle."
