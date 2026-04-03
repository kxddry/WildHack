"""Integration tests for prediction-service API routes."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.api.schemas import HealthResponse


def _make_app(model_loaded: bool = False) -> FastAPI:
    """Build a minimal FastAPI app with the prediction router and mocked state."""
    app = FastAPI()
    app.include_router(router)

    mock_model = MagicMock()
    mock_model.is_loaded = model_loaded

    app.state.model_manager = mock_model
    app.state.startup_time = time.time()
    return app


@pytest.fixture
def client_no_model():
    """TestClient with model NOT loaded and DB mocked as disconnected."""
    app = _make_app(model_loaded=False)
    with (
        patch("app.api.routes.postgres.check_connection", new_callable=AsyncMock, return_value=False),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture
def client_model_loaded():
    """TestClient with model loaded and DB mocked as connected."""
    app = _make_app(model_loaded=True)
    with (
        patch("app.api.routes.postgres.check_connection", new_callable=AsyncMock, return_value=True),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


class TestHealthEndpoint:
    def test_health_endpoint_returns_200(self, client_no_model):
        response = client_no_model.get("/health")
        assert response.status_code == 200

    def test_health_response_schema(self, client_no_model):
        response = client_no_model.get("/health")
        data = response.json()
        assert "status" in data
        assert "model_loaded" in data
        assert "database_connected" in data
        assert "uptime_seconds" in data

    def test_health_degraded_when_model_not_loaded(self, client_no_model):
        response = client_no_model.get("/health")
        data = response.json()
        assert data["status"] == "degraded"
        assert data["model_loaded"] is False

    def test_health_healthy_when_model_loaded_and_db_connected(self, client_model_loaded):
        response = client_model_loaded.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["model_loaded"] is True
        assert data["database_connected"] is True


class TestModelInfoWithoutModel:
    def test_model_info_without_model_returns_503(self, client_no_model):
        """GET /model/info should return 503 when model is not loaded."""
        response = client_no_model.get("/model/info")
        assert response.status_code == 503

    def test_model_info_error_detail(self, client_no_model):
        response = client_no_model.get("/model/info")
        data = response.json()
        assert "detail" in data
