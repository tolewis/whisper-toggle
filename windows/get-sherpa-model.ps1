param(
    [string]$ModelRoot = "$env:LOCALAPPDATA\Whisper Toggle\models",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ModelName = "sherpa-onnx-streaming-zipformer-en-2023-06-26"
# Source documented by k2-fsa sherpa-onnx pre-trained online transducer models:
# https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html
$ModelUrl = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$ModelName.tar.bz2"

$ModelRoot = [System.IO.Path]::GetFullPath($ModelRoot)
$ArchivePath = Join-Path $ModelRoot "$ModelName.tar.bz2"
$ModelDir = Join-Path $ModelRoot $ModelName

New-Item -ItemType Directory -Force -Path $ModelRoot | Out-Null

if ((Test-Path (Join-Path $ModelDir "encoder.onnx")) -and -not $Force) {
    Write-Host "Sherpa model already present: $ModelDir"
    Write-Host "Set WHISPER_SHERPA_MODEL_DIR=$ModelDir"
    exit 0
}

if ($Force -and (Test-Path $ModelDir)) {
    Remove-Item $ModelDir -Recurse -Force
}

Write-Host "Downloading $ModelName ..."
Invoke-WebRequest -Uri $ModelUrl -OutFile $ArchivePath

Write-Host "Extracting to $ModelRoot ..."
tar -xjf $ArchivePath -C $ModelRoot
Remove-Item $ArchivePath -Force

$Encoder = Join-Path $ModelDir "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx"
$Decoder = Join-Path $ModelDir "decoder-epoch-99-avg-1-chunk-16-left-128.onnx"
$Joiner = Join-Path $ModelDir "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx"
$Tokens = Join-Path $ModelDir "tokens.txt"

foreach ($Path in @($Encoder, $Decoder, $Joiner, $Tokens)) {
    if (-not (Test-Path $Path)) {
        throw "Expected model file missing after extraction: $Path"
    }
}

Copy-Item $Encoder (Join-Path $ModelDir "encoder.onnx") -Force
Copy-Item $Decoder (Join-Path $ModelDir "decoder.onnx") -Force
Copy-Item $Joiner (Join-Path $ModelDir "joiner.onnx") -Force

Write-Host "Sherpa model ready: $ModelDir"
Write-Host "Set WHISPER_SHERPA_MODEL_DIR=$ModelDir"
