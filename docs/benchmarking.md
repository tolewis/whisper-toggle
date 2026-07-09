# Whisper Toggle benchmarking

Benchmark in two layers. They answer different questions and should not be mixed.

## 1. Deterministic engine benchmark

This feeds the same WAV file directly to the local Whisper Toggle API. It measures model/runtime behavior without Windows focus, hotkeys, microphone routing, or paste.

On Windows, create a repeatable speech WAV with built-in SAPI:

```powershell
powershell -ExecutionPolicy Bypass -File windows\make-benchmark-audio.ps1 `
  -Text "Whisper Toggle benchmark phrase. The quick brown fox dictates into the focused window." `
  -Out C:\Temp\wt-benchmark.wav
```

For model selection, prefer a small corpus over one phrase:

```powershell
powershell -ExecutionPolicy Bypass -File windows\make-benchmark-corpus.ps1 `
  -OutDir C:\Temp\wt-corpus
```

Then run the API benchmark against a running tray/API:

```powershell
python scripts\benchmark_whisper_toggle.py `
  --audio C:\Temp\wt-benchmark.wav `
  --expected "Whisper Toggle benchmark phrase. The quick brown fox dictates into the focused window." `
  --model small.en `
  --stream `
  --runs 3 `
  --json-out C:\Temp\wt-benchmark.json
```

Primary metrics:
- `runtime`: device/model/compute actually running.
- `batch.elapsed_sec`: latency after the user stops speaking.
- `stream.first_partial_sec`: first live partial latency.
- `stream.final_sec`: stream final latency.
- `wer`: strict word error rate against the expected phrase.
- `dictation_wer` in candidate-model reports: WER after conservative dictation normalization for common number formats (`ninth` vs `9`, `fifteen` vs `15`).

## 2. Direct candidate-model benchmark

Use this before changing product architecture. It measures warmed model speed and accuracy directly, one backend/model at a time.

```powershell
python scripts\benchmark_asr_candidates.py `
  --manifest C:\Temp\wt-corpus\manifest.json `
  --models tiny.en,base.en,small.en,distil-large-v3 `
  --device cuda `
  --compute-type int8 `
  --runs 3 `
  --json-out C:\Temp\asr-candidates.json
```

See `docs/asr-backend-research.md` for the backend shortlist.

## 3. Desktop dictation benchmark (Windows Voice Typing vs Whisper Toggle)

Windows Voice Typing (`Win+H`) is not exposed as a normal API that accepts a WAV file. A fair comparison therefore needs a real interactive Windows desktop plus controlled audio routed into the default microphone.

The proper setup is:
1. Use a fixed WAV phrase (generated above).
2. Route playback into the default capture device using Stereo Mix, the machine's speaker→mic path, or a virtual audio cable.
3. Launch a benchmark text field in the active desktop session.
4. For Windows Voice Typing: focus the field, press `Win+H`, play the WAV, wait for inserted text, record latency/text.
5. For Whisper Toggle: focus the field, press `Ctrl+Shift+H`, play the same WAV, press `Ctrl+Shift+H`, record latency/text.
6. Restore the original default input/output device after the run if changed.

The repository includes an active-desktop harness for steps 3-5:

```powershell
python windows\desktop-dictation-benchmark.py `
  --mode windows `
  --audio C:\Temp\wt-benchmark.wav `
  --json-out C:\Temp\windows-voice-typing-benchmark.json

python windows\desktop-dictation-benchmark.py `
  --mode whisper-toggle `
  --audio C:\Temp\wt-benchmark.wav `
  --json-out C:\Temp\whisper-toggle-desktop-benchmark.json
```

This layer measures the end-user product path: hotkey ownership, first-word capture, streaming/final behavior, insertion success, leaked keys, and stuck modifiers. It cannot be proven from a plain SSH/session-0 process; it must run in the active console session.

If Stereo Mix is not present or does not capture the selected output, install/use a virtual audio cable. That driver install is a machine setting change and should be gated before automation changes defaults.
