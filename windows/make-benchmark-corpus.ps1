param(
  [string]$OutDir = "$env:TEMP\whisper-toggle-benchmark-corpus",
  [int]$Rate = 0
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$phrases = @(
  [pscustomobject]@{ id='dictation-basic'; text='Whisper Toggle benchmark phrase. The quick brown fox dictates into the focused window.' },
  [pscustomobject]@{ id='punctuation'; text='Please write this down, then add a comma, a period, and a question mark.' },
  [pscustomobject]@{ id='technical'; text='Restart the CUDA service and verify faster whisper is still using int eight compute.' },
  [pscustomobject]@{ id='terminal'; text='Run git status, then commit the benchmark results to main.' },
  [pscustomobject]@{ id='numbers'; text='Schedule the meeting for July ninth at ten thirty AM and remind me in fifteen minutes.' }
)

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = $Rate
$manifest = @()
try {
  foreach ($row in $phrases) {
    $wav = Join-Path $OutDir ($row.id + '.wav')
    $synth.SetOutputToWaveFile($wav)
    $synth.Speak($row.text)
    $synth.SetOutputToNull()
    $manifest += [pscustomobject]@{ id=$row.id; audio=(Resolve-Path $wav).Path; expected=$row.text }
  }
} finally {
  $synth.SetOutputToNull()
  $synth.Dispose()
}

$manifestPath = Join-Path $OutDir 'manifest.json'
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $manifestPath
[pscustomobject]@{ outDir=(Resolve-Path $OutDir).Path; manifest=(Resolve-Path $manifestPath).Path; clips=$manifest.Count } | ConvertTo-Json -Compress
