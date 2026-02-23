@echo off
title Whisper API Server
echo Starting Whisper API on 127.0.0.1:8788 ...
echo.

set WHISPER_API_DEFAULT_MODEL=small.en
set WHISPER_API_DEVICE=cuda
set WHISPER_API_COMPUTE_TYPE=int8
set WHISPER_API_LANGUAGE=en

REM Try venv first, fall back to system Python
if exist "%LOCALAPPDATA%\whisper-venv\Scripts\python.exe" (
    "%LOCALAPPDATA%\whisper-venv\Scripts\python" -m uvicorn app:app --host 127.0.0.1 --port 8788
) else (
    python -m uvicorn app:app --host 127.0.0.1 --port 8788
)

pause
