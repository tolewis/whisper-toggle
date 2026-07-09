from __future__ import annotations

from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "scripts" / "deploy.sh"


@pytest.fixture(scope="module")
def deploy_text() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def test_health_check_uses_real_health_path(deploy_text: str):
    # app.py serves GET /health, not /v1/health.
    assert "/v1/health" not in deploy_text
    assert "/health" in deploy_text


def test_health_check_fails_on_http_error(deploy_text: str):
    # curl -s returns 0 even on a 404; -f makes curl fail on HTTP errors so
    # the health check is meaningful.
    assert "curl -fsS" in deploy_text


def test_systemctl_guidance_matches_user_unit(deploy_text: str):
    # whisper-api installs as a --user unit; guidance must not tell the
    # operator to `sudo systemctl restart`.
    assert "sudo systemctl restart whisper-api" not in deploy_text
    assert "systemctl --user" in deploy_text
