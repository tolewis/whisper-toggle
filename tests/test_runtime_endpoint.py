"""Engine /v1/runtime contract tests with model load mocked."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as whisper_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WHISPER_API_DEVICE", "cpu")
    monkeypatch.setenv("WHISPER_API_COMPUTE_TYPE", "int8")
    monkeypatch.setenv("WHISPER_API_DEFAULT_MODEL", "base.en")
    # Avoid real device probing side effects in unit context if any
    return TestClient(whisper_app.app)


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_runtime_reports_device_model_compute(client):
    r = client.get("/v1/runtime")
    assert r.status_code == 200
    body = r.json()
    assert "device" in body
    assert "compute_type" in body
    assert "model" in body
    assert "backend" in body
    assert "streaming" in body
    assert body["streaming"] is True
    assert body["version"].startswith("2.")


def test_runtime_streaming_defaults_to_true(monkeypatch):
    # Unset / default -> streaming advertised as enabled.
    monkeypatch.delenv("WHISPER_API_STREAMING", raising=False)
    body = TestClient(whisper_app.app).get("/v1/runtime").json()
    assert body["streaming"] is True


def test_runtime_streaming_reflects_env_disabled(monkeypatch):
    # Operator disabled streaming -> the field must reflect reality, not a
    # hardcoded literal.
    monkeypatch.setenv("WHISPER_API_STREAMING", "0")
    body = TestClient(whisper_app.app).get("/v1/runtime").json()
    assert body["streaming"] is False
