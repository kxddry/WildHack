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
