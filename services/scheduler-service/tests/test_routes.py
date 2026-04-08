"""Route tests for scheduler-service auth-protected control endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.config import settings


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", "internal-secret")

    orchestrator = SimpleNamespace(
        status={"running": False},
        run_prediction_cycle=AsyncMock(return_value={"status": "pipeline_ok"}),
    )
    quality_checker = SimpleNamespace(
        status={"running": False},
        _alerts=[],
        _last_metrics={},
        run_quality_check=AsyncMock(return_value={"status": "quality_ok"}),
    )

    app = FastAPI()
    app.state.db = object()
    app.state.orchestrator = orchestrator
    app.state.quality_checker = quality_checker
    app.include_router(router)

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client, orchestrator, quality_checker


@pytest.mark.parametrize("path", ["/pipeline/trigger", "/quality/trigger"])
def test_control_routes_reject_missing_token(client, path: str) -> None:
    test_client, orchestrator, quality_checker = client

    response = test_client.post(path)

    assert response.status_code == 401
    assert orchestrator.run_prediction_cycle.await_count == 0
    assert quality_checker.run_quality_check.await_count == 0


@pytest.mark.parametrize("path", ["/pipeline/trigger", "/quality/trigger"])
def test_control_routes_reject_invalid_token(client, path: str) -> None:
    test_client, orchestrator, quality_checker = client

    response = test_client.post(path, headers={"X-Internal-Token": "wrong"})

    assert response.status_code == 401
    assert orchestrator.run_prediction_cycle.await_count == 0
    assert quality_checker.run_quality_check.await_count == 0


def test_pipeline_trigger_accepts_valid_token(client) -> None:
    test_client, orchestrator, _quality_checker = client

    response = test_client.post(
        "/pipeline/trigger",
        headers={"X-Internal-Token": "internal-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "pipeline_ok"}
    assert orchestrator.run_prediction_cycle.await_count == 1


def test_quality_trigger_accepts_valid_token(client) -> None:
    test_client, _orchestrator, quality_checker = client

    response = test_client.post(
        "/quality/trigger",
        headers={"X-Internal-Token": "internal-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "quality_ok"}
    assert quality_checker.run_quality_check.await_count == 1
