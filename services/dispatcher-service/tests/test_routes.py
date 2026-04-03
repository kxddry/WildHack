"""Integration tests for dispatcher-service API routes."""

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router


def _make_app() -> FastAPI:
    """Build a minimal FastAPI app with the dispatcher router and mocked state."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client():
    """TestClient with postgres mocked as connected."""
    app = _make_app()
    with patch("app.api.routes.postgres.check_connection", new_callable=AsyncMock, return_value=True):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture
def client_db_down():
    """TestClient with postgres mocked as disconnected."""
    app = _make_app()
    with patch("app.api.routes.postgres.check_connection", new_callable=AsyncMock, return_value=False):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


class TestHealthEndpoint:
    def test_health_endpoint_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_response_schema(self, client):
        data = client.get("/health").json()
        assert "status" in data
        assert "database_connected" in data
        assert "uptime_seconds" in data

    def test_health_healthy_when_db_up(self, client):
        data = client.get("/health").json()
        assert data["status"] == "healthy"
        assert data["database_connected"] is True

    def test_health_degraded_when_db_down(self, client_db_down):
        data = client_db_down.get("/health").json()
        assert data["status"] == "degraded"
        assert data["database_connected"] is False
