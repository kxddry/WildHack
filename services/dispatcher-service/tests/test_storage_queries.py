"""Tests for SQL-backed dispatcher storage query shapes."""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.storage import postgres


class _FakeRow:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchall(self) -> list[_FakeRow]:
        return [_FakeRow(row) for row in self._rows]


class _FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.statements: list[str] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return _FakeResult(self._rows)

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeEngine:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def connect(self) -> _FakeConn:
        return self._conn


def test_transport_requests_window_query_supports_legacy_ts(monkeypatch) -> None:
    conn = _FakeConn(
        [
            {
                "id": 1,
                "office_from_id": 7,
                "time_window_start": datetime(2025, 6, 1, 8, 0, 0),
                "time_window_end": datetime(2025, 6, 1, 8, 30, 0),
                "total_predicted_units": 88.5,
                "vehicles_required": 3,
                "status": "planned",
                "created_at": datetime(2025, 6, 1, 7, 45, 0),
                "routes": [101, 202],
            }
        ]
    )
    monkeypatch.setattr(postgres, "get_engine", lambda: _FakeEngine(conn))

    rows = asyncio.run(
        postgres.get_transport_requests_window(
            office_id=7,
            range_from=datetime(2025, 6, 1, 0, 0, 0),
            range_to=datetime(2025, 6, 2, 0, 0, 0),
        )
    )

    assert rows[0]["routes"] == [101, 202]
    assert "COALESCE(elem ->> 'timestamp', elem ->> 'ts')::timestamp AS step_ts" in conn.statements[0]


def test_get_all_warehouses_aggregates_each_source_in_subqueries(monkeypatch) -> None:
    conn = _FakeConn(
        [
            {
                "warehouse_id": 42,
                "name": "North Hub",
                "route_count": 15,
                "latest_forecast_at": datetime(2025, 6, 1, 12, 30, 0),
                "upcoming_trucks": 7,
            }
        ]
    )
    monkeypatch.setattr(postgres, "get_engine", lambda: _FakeEngine(conn))

    rows = asyncio.run(postgres.get_all_warehouses())

    assert rows == [
        {
            "warehouse_id": 42,
            "name": "North Hub",
            "route_count": 15,
            "latest_forecast_at": datetime(2025, 6, 1, 12, 30, 0),
            "upcoming_trucks": 7,
        }
    ]
    statement = conn.statements[0]
    assert "LEFT JOIN routes r ON r.warehouse_id = w.warehouse_id" not in statement
    assert "LEFT JOIN forecasts f ON f.warehouse_id = w.warehouse_id" not in statement
    assert "LEFT JOIN transport_requests tr ON tr.warehouse_id = w.warehouse_id" not in statement
    assert "SELECT warehouse_id, COUNT(DISTINCT route_id) AS route_count" in statement
    assert "SELECT warehouse_id, MAX(created_at) AS latest_forecast_at" in statement
    assert "SELECT warehouse_id, COALESCE(SUM(trucks_needed), 0) AS upcoming_trucks" in statement
