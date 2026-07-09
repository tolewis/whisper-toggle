param(
  [string]$Text = "Whisper Toggle benchmark phrase. The quick brown fox dictates into the focused window.",
  [string]$Out = "$env:TEMP\whisper-toggle-benchmark.wav",
  [int]$Rate = 0
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = $Rate
$dir = Split-Path -Parent $Out
if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$synth.SetOutputToWaveFile($Out)
try {
  $synth.Speak($Text)
} finally {
  $synth.SetOutputToNull()
  $synth.Dispose()
}
[pscustomobject]@{ path = (Resolve-Path $Out).Path; text = $Text } | ConvertTo-Json -Compress
