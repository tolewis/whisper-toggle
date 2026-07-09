# ASR backend research and selection notes

Goal: find the fastest accurate local transcription backend for Whisper Toggle,
with live partials and reliable foreground insertion. Current stack is
`faster-whisper` + CTranslate2 behind a local FastAPI process.

## Current measured baseline

On jubiku with the warmed v2.0.3 API (`small.en`, CUDA, int8), a fixed 6.458s
SAPI WAV produced:

- Batch final median: ~0.484s after audio upload, WER 0.0.
- Streaming first partial median: ~0.768s, WER 0.0 final.
- Streaming final median: ~8.727s, i.e. slower than the audio duration because
  the current `whisper_streaming` integration prioritizes stable confirmation
  over ultra-low latency.

Interpretation: raw warm transcription is already fast on NVIDIA. The product
latency gap vs Windows Voice Typing is more about streaming endpointing,
partial-commit policy, and Windows text insertion than model load/inference
alone.

## GitHub projects worth studying

Snapshot gathered 2026-07-09 with `gh repo view` / `gh search repos`.

| Project | Activity signal | Why it matters | Initial call |
|---|---:|---|---|
| `k2-fsa/sherpa-onnx` | pushed 2026-07-09, ~13k stars | True streaming/offline ASR with ONNX Runtime, websocket examples, many platforms. Zipformer/transducer models should be benchmarked for sub-second partials. | Top candidate for live dictation backend. |
| `moonshine-ai/moonshine` | pushed 2026-07-09, ~8.6k stars | Described as very low latency speech-to-text for voice agents. Need verify Windows packaging and model/API maturity. | Top research candidate. |
| `ggml-org/whisper.cpp` | pushed 2026-07-01, ~51k stars | Mature local Whisper inference, quantized models, C/C++ deployment, examples. May be good for CPU/iGPU and simple packaging. | Benchmark as local fallback/alternate backend. |
| `SYSTRAN/faster-whisper` | pushed 2025-11-19, ~24k stars | Current inference stack. Easy to test model sizes/distil variants while keeping API stable. | Keep as baseline; benchmark distil models. |
| `ufal/SimulStreaming` | pushed 2026-07-05, ~600 stars | Successor-ish research direction to `whisper_streaming`; designed for simultaneous/streaming transcription. | Study for algorithm, maybe replace current stream loop. |
| `ufal/whisper_streaming` | pushed 2025-11-12, ~3.6k stars | Current streaming integration; good but final latency is high in our baseline. | Keep until replaced, but not the end state. |
| `KoljaB/RealtimeSTT` | pushed 2026-06-12, ~10k stars | Python realtime STT wrapper with VAD/wake/instant transcription. Uses faster-whisper-style components and has UX ideas. | Mine for VAD/threading patterns. |
| `collabora/WhisperLive` | pushed 2026-07-06, ~4k stars | Nearly-live Whisper server/client architecture. | Mine for server/client streaming patterns. |
| `alphacep/vosk-api` | pushed 2026-07-02, ~15k stars | Older true streaming offline ASR. Very low latency but likely lower accuracy than Whisper-family models. | Benchmark only if speed trumps accuracy. |
| `openai/whisper`, `m-bain/whisperX` | active but not live-first | Accuracy/reference tooling, timestamps/diarization. | Not primary for low-latency dictation. |

## VibeVoice note

`microsoft/VibeVoice` and most `VibeVoice-Realtime-0.5B` wrappers found on
GitHub are text-to-speech / voice generation projects, not dictation ASR. They
are not candidates for replacing the transcription model unless a specific ASR
checkpoint/API is identified and verified.

## Architecture direction

Do not tie the product shell to any one ASR library. Keep the Windows tray,
settings, hotkey, overlay, and insertion logic, but make the engine backend
swappable:

```text
Tray / Controller / Insertion
  -> Local ASR service API
     -> backend=faster-whisper | sherpa-onnx | whisper.cpp | moonshine
```

Selection metrics:

1. Cold load time.
2. Warm model resident memory / VRAM.
3. Time to first partial.
4. Time from end-of-speech to final text.
5. WER on fixed phrases and real dictation clips.
6. Stability over 30+ repeated dictations.
7. Windows packaging complexity.

## Benchmark commands

Build a repeatable local SAPI corpus:

```powershell
powershell -ExecutionPolicy Bypass -File windows\make-benchmark-corpus.ps1 `
  -OutDir C:\src\wt-bench\corpus
```

Direct model benchmark for Faster-Whisper candidates:

```powershell
python scripts\benchmark_asr_candidates.py `
  --manifest C:\src\wt-bench\corpus\manifest.json `
  --models tiny.en,base.en,small.en,distil-large-v3 `
  --device cuda `
  --compute-type int8 `
  --runs 3 `
  --json-out C:\src\wt-bench\asr-candidates.json
```

For a fair Windows Voice Typing vs Whisper Toggle product benchmark, use the
routed-audio desktop harness from `docs/benchmarking.md`, but only from a path
that can prove the benchmark window owns foreground focus. Plain SSH/Scheduled
Task is not authoritative for global-hotkey UI measurement.

## Iteration-1 conclusion

The fastest near-term path is:

1. Benchmark Faster-Whisper model variants immediately (same backend, no product
   rewrite).
2. Prototype a `sherpa-onnx` streaming backend as the first real architecture
   challenger.
3. Keep final paste as the reliable default, but improve live overlay/partial
   latency; only consider focused live text mutation after the ASR backend and
   foreground authority are proven.

## Iteration-2 baseline results

On jubiku with cached Faster-Whisper models, CUDA/int8, beam size 1, single fixed
6.458s SAPI clip:

| Model | Load sec | Warm median sec | RTF median | WER |
|---|---:|---:|---:|---:|
| `tiny.en` | 0.880 | 0.143 | 0.022 | 0.0 |
| `base.en` | 0.583 | 0.190 | 0.029 | 0.0 |
| `small.en` | 1.393 | 0.311 | 0.048 | 0.0 |

For this simple phrase, `tiny.en` is fastest and accurate. That is **not enough**
to make it the default: we need a harder corpus and live/noisy microphone clips.
Iteration 2 added `windows/make-benchmark-corpus.ps1` and `--manifest` support
so selection can use multiple clips while loading each model only once.

Synthetic 5-clip corpus results on jubiku (`tiny.en`, `base.en`, `small.en`,
CUDA/int8, beam size 1, 2 measured runs per clip):

| Model | Load sec | Corpus median sec | Corpus median RTF | Corpus median WER | Max WER | Note |
|---|---:|---:|---:|---:|---:|---|
| `tiny.en` | 0.890 | 0.130 | 0.024 | 0.100 | 0.250 | Fastest; mishears `git` as `get`; numbers normalized differently. |
| `base.en` | 0.566 | 0.173 | 0.032 | 0.000 | 0.3125 | Best speed/accuracy tradeoff on synthetic corpus. |
| `small.en` | 1.498 | 0.313 | 0.058 | 0.000 | 0.375 | Slower, no synthetic accuracy win over `base.en`. |

## Iteration-3 dictation-normalized WER

Iteration 3 added `dictation_wer` to treat common dictation-equivalent number
forms as equal (`ninth` -> `9`, `fifteen` -> `15`, `10.30am` -> `10 30 am`).
This avoids rejecting useful outputs just because the ASR formats spoken numbers
like Windows Voice Typing does.

Rerun on the same 5-clip corpus (`C:\src\wt-bench\asr-candidates-corpus-fw-cuda-int8-dictation-wer.json`):

| Model | Corpus median sec | Corpus median RTF | Median dictation WER | Max dictation WER | Remaining error pattern |
|---|---:|---:|---:|---:|---|
| `tiny.en` | 0.128 | 0.0235 | 0.000 | 0.100 | `git status` -> `get status`; `int` -> `in`. |
| `base.en` | 0.173 | 0.0321 | 0.000 | 0.1429 | `int eight compute` -> `intate compute`. |
| `small.en` | 0.313 | 0.0581 | 0.000 | 0.1429 | same `intate compute`; slower. |

Current synthetic-corpus recommendation: `tiny.en` is now the leading low-latency
candidate if we can tolerate occasional technical-term confusions. `base.en` is
the safer default for technical dictation. `small.en` is not justified by these
synthetic clips.

## Iteration-4 deterministic noise stress pass

Iteration 4 added `scripts/augment_benchmark_corpus.py`, which creates a
repeatable noisy corpus from the clean SAPI manifest using deterministic white
noise. This does **not** replace real microphone clips, but it gives a stable
stress pass before changing defaults.

Jubiku artifacts:
- Noisy corpus: `C:\src\wt-bench\corpus-noise15\manifest.json`
- Results: `C:\src\wt-bench\asr-candidates-corpus-noise15-fw-cuda-int8.json`

Faster-Whisper CUDA/int8, beam size 1, 15 dB white-noise corpus, 2 measured runs
per clip:

| Model | Corpus median sec | Corpus median RTF | Median dictation WER | Max dictation WER | Noise-specific failures |
|---|---:|---:|---:|---:|---|
| `tiny.en` | 0.133 | 0.0246 | 0.0769 | 0.1429 | `fox` -> `box`; `git` -> `Get`; `verify` -> `verified`. |
| `base.en` | 0.175 | 0.0323 | 0.000 | 0.0769 | `fox` -> `box`; `int eight` -> `in 8`. |
| `small.en` | 0.315 | 0.0576 | 0.000 | 0.1429 | `int eight` -> `Intate`; otherwise robust but slower. |

Noise-stress recommendation: keep `base.en` as the best current default-quality
candidate. `tiny.en` remains attractive for a low-latency mode but degraded on
noise. `small.en` again fails to justify its added latency.

## Iteration-5 sherpa-onnx online prototype

Iteration 5 added `--backend sherpa-onnx-online` to
`scripts/benchmark_asr_candidates.py`. The benchmark now accepts extracted
sherpa-onnx online transducer model directories and reports:

- `elapsed_sec`: final decode CPU time under fast file replay.
- `first_partial_sec`: CPU time until first non-empty partial under fast replay.
- `first_partial_audio_sec`: amount of source audio consumed before that first
  non-empty partial; this is the better lower bound for live UX.

Jubiku setup used an isolated benchmark venv, not the installed product runtime:
`C:\src\wt-bench\sherpa-env` with `sherpa-onnx==1.13.4` and `numpy`.

Jubiku artifacts:
- Model dirs under `C:\src\wt-bench\sherpa-models\`
- Results: `C:\src\wt-bench\asr-candidates-corpus-sherpa-onnx-online-cpu.json`

Sherpa-ONNX online transducer, CPU provider, 1 thread, 0.32s chunks, 2 measured
runs per clip:

| Model | Load sec | Corpus median sec | Median RTF | Median first partial audio | Median dictation WER | Max dictation WER | Notes |
|---|---:|---:|---:|---:|---:|---:|---|
| `streaming-zipformer-en-20M-2023-02-17` | 0.937 | 0.213 | 0.0388 | 1.28s | 0.2308 | 0.5000 | Too inaccurate; drops/misrecognizes initial words. |
| `streaming-zipformer-en-kroko-2025-08-06` | 3.926 | 0.241 | 0.0449 | 1.60s | 0.0000 | 0.2143 | Accurate on simple/numbers clips; misses technical terms (`CUDA` -> `KUDA`, `git` -> `get`, `int eight` -> `intate`). |

Conclusion: sherpa-onnx is viable as a true-streaming prototype and runs fast on
CPU, but neither tested model beats Faster-Whisper `base.en` as the default
quality backend on the current synthetic corpus. The useful finding is the live
partial behavior: KROKO produces stable partial text after ~1.6s of audio with
~60ms compute overhead, much better than the current Whisper Streaming final
path. Next steps are noise/real-mic tests for KROKO, then decide whether to use
sherpa for overlay-only live partials while keeping Faster-Whisper final paste.
