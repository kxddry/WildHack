"""
E2E smoke test — run against docker-compose services.
Usage: pytest tests/e2e/test_smoke.py -v
Requires: docker-compose up running
"""
import httpx
import pytest

BASE_PREDICTION = "http://localhost:8000"
BASE_DISPATCHER = "http://localhost:8001"
BASE_DASHBOARD = "http://localhost:4000"


@pytest.mark.e2e
class TestSmoke:
    def test_prediction_health(self):
        r = httpx.get(f"{BASE_PREDICTION}/health", timeout=10)
        assert r.status_code == 200

    def test_dispatcher_health(self):
        r = httpx.get(f"{BASE_DISPATCHER}/health", timeout=10)
        assert r.status_code == 200

    def test_dashboard_accessible(self):
        r = httpx.get(BASE_DASHBOARD, timeout=10)
        assert r.status_code == 200

    def test_prediction_openapi(self):
        r = httpx.get(f"{BASE_PREDICTION}/openapi.json", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "/predict" in data["paths"]

    def test_dispatcher_openapi(self):
        r = httpx.get(f"{BASE_DISPATCHER}/openapi.json", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "/dispatch" in data["paths"]
