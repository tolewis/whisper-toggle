# Best-effort: reduce conflict between Win+H and Windows Voice Typing.
# Whisper Toggle binds Win+H with a low-level suppress hook; this also turns off
# the OS voice-typing startup tip where possible.
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

Write-Host "Whisper Toggle: Win+H is owned by the tray app while it is running."
Write-Host "If Windows Voice Typing still appears, open Settings > Time & language > Typing > Voice typing and turn off 'Voice typing launcher'."
