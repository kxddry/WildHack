"""Integration tests for dispatcher-service API routes."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from app.api.routes_v1 import router as router_v1


def _make_app() -> FastAPI:
    """Build a minimal FastAPI app mirroring the production routing layout."""
    app = FastAPI()
    app.include_router(router)
    app.include_router(router, prefix="/api/v1")
    app.include_router(router_v1, prefix="/api/v1")
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

    def test_health_also_served_under_v1_prefix(self, client):
        # PRD §6.3 requires the same health endpoint under /api/v1.
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestWarehousesEndpoint:
    def test_returns_warehouse_summary_shape(self, client):
        sample_row = {
            "warehouse_id": 42,
            "name": "North Hub",
            "route_count": 15,
            "latest_forecast_at": "2025-06-01T12:30:00",
            "upcoming_trucks": 7,
        }
        with patch(
            "app.api.routes.postgres.get_all_warehouses",
            new_callable=AsyncMock,
            return_value=[sample_row],
        ):
            response = client.get("/warehouses")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["warehouses"] == [
            {
                "warehouse_id": 42,
                "name": "North Hub",
                "route_count": 15,
                "latest_forecast_at": "2025-06-01T12:30:00",
                "upcoming_trucks": 7,
            }
        ]


# ---------------------------------------------------------------------------
# /api/v1/transport-requests (PRD §6.2)
# ---------------------------------------------------------------------------


class TestTransportRequestsV1:
    def test_returns_prd_shape(self, client):
        sample_row = {
            "id": 42,
            "office_from_id": 7,
            "time_window_start": "2025-06-01T08:00:00",
            "time_window_end": "2025-06-01T10:00:00",
            "total_predicted_units": 88.5,
            "vehicles_required": 3,
            "status": "planned",
            "created_at": "2025-06-01T07:30:00",
            "routes": [101, 202],
        }
        with patch(
            "app.api.routes_v1.postgres.get_transport_requests_window",
            new_callable=AsyncMock,
            return_value=[sample_row],
        ):
            response = client.get(
                "/api/v1/transport-requests",
                params={
                    "office_id": 7,
                    "from": "2025-06-01T00:00:00",
                    "to": "2025-06-02T00:00:00",
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["office_id"] == 7
        item = body["items"][0]
        # Schema mandated by PRD §6.2
        for key in (
            "id",
            "office_from_id",
            "time_window_start",
            "time_window_end",
            "routes",
            "total_predicted_units",
            "vehicles_required",
            "status",
            "created_at",
        ):
            assert key in item
        assert item["routes"] == [101, 202]
        assert item["vehicles_required"] == 3

    def test_invalid_range_returns_422(self, client):
        with patch(
            "app.api.routes_v1.postgres.get_transport_requests_window",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = client.get(
                "/api/v1/transport-requests",
                params={
                    "office_id": 7,
                    "from": "2025-06-02T00:00:00",
                    "to": "2025-06-01T00:00:00",
                },
            )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# /api/v1/metrics/business (PRD §9.2)
# ---------------------------------------------------------------------------


class TestBusinessMetricsV1:
    def test_returns_kpi_shape_when_data_present(self, client):
        with patch(
            "app.api.routes_v1.postgres.get_business_metrics",
            new_callable=AsyncMock,
            return_value={
                "order_accuracy": 0.85,
                "avg_truck_utilization": 0.72,
                "n_slots_evaluated": 20,
                "n_slots_total": 25,
                "truck_capacity": 33,
            },
        ):
            response = client.get("/api/v1/metrics/business")
        assert response.status_code == 200
        body = response.json()
        assert body["order_accuracy"] == pytest.approx(0.85)
        assert body["avg_truck_utilization"] == pytest.approx(0.72)
        assert body["n_slots_evaluated"] == 20
        assert body["n_slots_total"] == 25
        assert body["note"] is None

    def test_note_populated_when_no_actuals(self, client):
        with patch(
            "app.api.routes_v1.postgres.get_business_metrics",
            new_callable=AsyncMock,
            return_value={
                "order_accuracy": 0.0,
                "avg_truck_utilization": 0.0,
                "n_slots_evaluated": 0,
                "n_slots_total": 5,
                "truck_capacity": 0,
            },
        ):
            response = client.get("/api/v1/metrics/business")
        assert response.status_code == 200
        body = response.json()
        assert body["n_slots_evaluated"] == 0
        assert body["note"] is not None
