"""Storage-level tests for authoritative upload snapshot replacement."""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.storage import postgres


class _FakeResult:
    def __init__(
        self,
        *,
        scalar_value=None,
        rowcount: int | None = None,
        mapping_rows: list[dict] | None = None,
    ) -> None:
        self._scalar_value = scalar_value
        self.rowcount = rowcount
        self._mapping_rows = mapping_rows or []

    def scalar(self):
        return self._scalar_value

    def mappings(self):
        class _Mappings:
            def __init__(self, rows: list[dict]) -> None:
                self._rows = rows

            def all(self) -> list[dict]:
                return self._rows

            def first(self):
                return self._rows[0] if self._rows else None

        return _Mappings(self._mapping_rows)


class _FakeConn:
    def __init__(self, results: list[_FakeResult]) -> None:
        self._results = iter(results)
        self.statements: list[str] = []
        self.params: list[dict | list[dict] | None] = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement).strip())
        self.params.append(params)
        return next(self._results)


class _FakeBegin:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeEngine:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self._conn)

    def connect(self) -> _FakeBegin:
        return _FakeBegin(self._conn)


def test_refresh_snapshot_replaces_existing_history(monkeypatch) -> None:
    conn = _FakeConn(
        [
            _FakeResult(scalar_value=5),
            _FakeResult(rowcount=2),
            _FakeResult(rowcount=3),
            _FakeResult(rowcount=4),
            _FakeResult(rowcount=2),
            _FakeResult(rowcount=5),
            _FakeResult(rowcount=3),
            _FakeResult(rowcount=2),
            _FakeResult(rowcount=2),
            _FakeResult(scalar_value=2),
            _FakeResult(scalar_value=2),
            _FakeResult(scalar_value=3),
        ]
    )
    monkeypatch.setattr(postgres, "_get_async_engine", lambda: _FakeEngine(conn))

    retained_rows = [
        [
            {
                "route_id": 10,
                "warehouse_id": 1,
                "timestamp": datetime(2025, 1, 1, 0, 0, 0),
                "status_1": 1.0,
                "status_2": 0.0,
                "status_3": 0.0,
                "status_4": 0.0,
                "status_5": 0.0,
                "status_6": 0.0,
                "status_7": 0.0,
                "status_8": 0.0,
                "target_2h": 10.0,
            },
            {
                "route_id": 10,
                "warehouse_id": 1,
                "timestamp": datetime(2025, 1, 1, 0, 30, 0),
                "status_1": 2.0,
                "status_2": 0.0,
                "status_3": 0.0,
                "status_4": 0.0,
                "status_5": 0.0,
                "status_6": 0.0,
                "status_7": 0.0,
                "status_8": 0.0,
                "target_2h": 11.0,
            },
            {
                "route_id": 20,
                "warehouse_id": 2,
                "timestamp": datetime(2025, 1, 1, 0, 30, 0),
                "status_1": 3.0,
                "status_2": 0.0,
                "status_3": 0.0,
                "status_4": 0.0,
                "status_5": 0.0,
                "status_6": 0.0,
                "status_7": 0.0,
                "status_8": 0.0,
                "target_2h": 12.0,
            },
        ]
    ]

    summary = asyncio.run(
        postgres.refresh_snapshot(
            history_chunks=retained_rows,
            retention_cutoff=datetime(2024, 12, 25, 0, 30, 0),
            retention_days=7,
            rows_received=6,
        )
    )

    delete_history_idx = next(
        i
        for i, sql in enumerate(conn.statements)
        if sql == "DELETE FROM route_status_history"
    )
    insert_history_idx = next(
        i for i, sql in enumerate(conn.statements) if "INSERT INTO route_status_history" in sql
    )

    assert delete_history_idx < insert_history_idx
    assert summary == {
        "rows_before": 5,
        "rows_after": 3,
        "rows_inserted": 3,
        "rows_submitted": 3,
        "retention_cutoff": "2024-12-25T00:30:00",
        "retention_days": 7,
        "pruned_history_rows": 8,
        "cleared_forecasts": 2,
        "cleared_transport_requests": 3,
        "routes_after": 2,
        "warehouses_after": 2,
    }


def test_get_route_history_windows_uses_partitioned_limit_query(monkeypatch) -> None:
    rows = [
        {
            "route_id": 10,
            "office_from_id": 1,
            "timestamp": datetime(2025, 1, 1, 0, 0, 0),
            "status_1": 1.0,
            "status_2": 0.0,
            "status_3": 0.0,
            "status_4": 0.0,
            "status_5": 0.0,
            "status_6": 0.0,
            "status_7": 0.0,
            "status_8": 0.0,
            "target_2h": 10.0,
        },
        {
            "route_id": 20,
            "office_from_id": 2,
            "timestamp": datetime(2025, 1, 1, 0, 30, 0),
            "status_1": 2.0,
            "status_2": 0.0,
            "status_3": 0.0,
            "status_4": 0.0,
            "status_5": 0.0,
            "status_6": 0.0,
            "status_7": 0.0,
            "status_8": 0.0,
            "target_2h": 11.0,
        },
    ]
    conn = _FakeConn([_FakeResult(mapping_rows=rows)])
    monkeypatch.setattr(postgres, "_get_async_engine", lambda: _FakeEngine(conn))

    result = asyncio.run(postgres.get_route_history_windows([10, 20], limit=288))

    assert result == rows
    assert "ROW_NUMBER() OVER" in conn.statements[0]
    assert conn.params[0] == {"route_ids": [10, 20], "limit": 288}
