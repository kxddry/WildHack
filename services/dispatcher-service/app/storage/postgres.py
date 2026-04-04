from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


async def create_engine_pool(database_url: str) -> AsyncEngine:
    global _engine
    _engine = create_async_engine(
        database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    return _engine


async def close_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialised — call create_engine_pool first")
    return _engine


async def save_transport_requests(requests: list[dict]) -> None:
    if not requests:
        return

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO transport_requests "
                "(warehouse_id, time_slot_start, time_slot_end, "
                "total_containers, truck_capacity, buffer_pct, "
                "trucks_needed, calculation, status) "
                "VALUES "
                "(:warehouse_id, :time_slot_start, :time_slot_end, "
                ":total_containers, :truck_capacity, :buffer_pct, "
                ":trucks_needed, :calculation, :status)"
            ),
            [
                {
                    "warehouse_id": r["warehouse_id"],
                    "time_slot_start": r["time_slot_start"].replace(tzinfo=None) if hasattr(r["time_slot_start"], 'tzinfo') and r["time_slot_start"].tzinfo else r["time_slot_start"],
                    "time_slot_end": r["time_slot_end"].replace(tzinfo=None) if hasattr(r["time_slot_end"], 'tzinfo') and r["time_slot_end"].tzinfo else r["time_slot_end"],
                    "total_containers": r["total_containers"],
                    "truck_capacity": r["truck_capacity"],
                    "buffer_pct": r["buffer_pct"],
                    "trucks_needed": r["trucks_needed"],
                    "calculation": r["calculation"],
                    "status": r["status"],
                }
                for r in requests
            ],
        )


async def get_schedule(warehouse_id: int) -> list[dict]:
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT warehouse_id, time_slot_start, time_slot_end, "
                "total_containers, truck_capacity, buffer_pct, "
                "trucks_needed, calculation, status "
                "FROM transport_requests "
                "WHERE warehouse_id = :warehouse_id "
                "AND status IN ('planned', 'dispatched') "
                "ORDER BY time_slot_start"
            ),
            {"warehouse_id": warehouse_id},
        )
        return [dict(row._mapping) for row in result.fetchall()]


async def get_recent_forecasts(
    warehouse_id: int,
    time_start: datetime,
    time_end: datetime,
) -> list[dict]:
    """Fetch forecasts for a warehouse within a time range.

    The forecasts table stores prediction steps as JSONB in the ``forecasts``
    column.  Each step contains a timestamp and predicted value.  This function
    extracts those steps and returns them in the ``{time_slot_start,
    time_slot_end, total_containers}`` format expected by
    :class:`DispatchCalculator`.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT route_id, anchor_ts, forecasts "
                "FROM forecasts "
                "WHERE warehouse_id = :warehouse_id "
                "AND anchor_ts >= :time_start "
                "AND anchor_ts <= :time_end "
                "ORDER BY anchor_ts"
            ),
            {
                "warehouse_id": warehouse_id,
                "time_start": time_start.replace(tzinfo=None) if time_start.tzinfo else time_start,
                "time_end": time_end.replace(tzinfo=None) if time_end.tzinfo else time_end,
            },
        )
        rows = [dict(row._mapping) for row in result.fetchall()]

    items: list[dict] = []
    for row in rows:
        forecasts_data = row["forecasts"]
        if isinstance(forecasts_data, str):
            forecasts_data = json.loads(forecasts_data)
        for step in forecasts_data:
            ts_raw = step.get("timestamp") or step.get("ts")
            pv = step.get("predicted_value")
            value = pv if pv is not None else step.get("value", 0.0)
            if ts_raw is None:
                continue
            if isinstance(ts_raw, str):
                ts_parsed = datetime.fromisoformat(ts_raw)
            else:
                ts_parsed = ts_raw
            if hasattr(ts_parsed, 'tzinfo') and ts_parsed.tzinfo:
                ts_parsed = ts_parsed.replace(tzinfo=None)
            items.append({
                "time_slot_start": ts_parsed,
                "time_slot_end": ts_parsed,
                "total_containers": float(value),
            })
    return items


async def get_all_warehouses() -> list[dict]:
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT w.warehouse_id, w.name, "
                "COUNT(DISTINCT r.route_id) AS route_count, "
                "MAX(f.created_at) AS latest_forecast_at, "
                "COALESCE(SUM(CASE WHEN tr.status IN ('planned', 'dispatched') "
                "THEN tr.trucks_needed ELSE 0 END), 0) AS upcoming_trucks "
                "FROM warehouses w "
                "LEFT JOIN routes r ON r.warehouse_id = w.warehouse_id "
                "LEFT JOIN forecasts f ON f.warehouse_id = w.warehouse_id "
                "LEFT JOIN transport_requests tr ON tr.warehouse_id = w.warehouse_id "
                "AND tr.status IN ('planned', 'dispatched') "
                "GROUP BY w.warehouse_id, w.name "
                "ORDER BY w.warehouse_id"
            )
        )
        return [dict(row._mapping) for row in result.fetchall()]


async def check_connection() -> bool:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("Database connection check failed", exc_info=True)
        return False
