"""Async PostgreSQL storage using SQLAlchemy async + asyncpg."""

import json
import logging
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


async def create_engine_pool(database_url: str) -> None:
    """Create the async engine with connection pooling."""
    global _engine
    _engine = create_async_engine(
        database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )
    logger.info("Database engine created for %s", database_url.split("@")[-1])


async def close_engine() -> None:
    """Dispose of the engine and close all connections."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("Database engine closed")


def _get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialized. Call create_engine_pool() first.")
    return _engine


async def save_forecasts(
    route_id: int,
    warehouse_id: int,
    anchor_ts: str,
    forecasts_json: list[dict[str, Any]],
    model_version: str,
) -> None:
    """Insert a forecast record into the forecasts table."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO forecasts (route_id, warehouse_id, anchor_ts, forecasts, model_version, created_at)
                VALUES (:route_id, :warehouse_id, :anchor_ts, :forecasts, :model_version, NOW())
                """
            ),
            {
                "route_id": route_id,
                "warehouse_id": warehouse_id,
                "anchor_ts": anchor_ts,
                "forecasts": json.dumps(forecasts_json),
                "model_version": model_version,
            },
        )


async def get_forecast_history(route_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """Return recent forecasts for a route."""
    engine = _get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT route_id, warehouse_id, anchor_ts, forecasts, model_version, created_at
                FROM forecasts
                WHERE route_id = :route_id
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"route_id": route_id, "limit": limit},
        )
        rows = result.mappings().all()
        return [dict(row) for row in rows]


async def get_route_status_history(route_id: int, limit: int = 288) -> pd.DataFrame:
    """Query route_status_history table, return DataFrame with status columns.

    Returns DataFrame with columns: timestamp, status_1..8, target_2h.
    Ordered by timestamp DESC, limited to last N rows.
    """
    engine = _get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT timestamp, route_id, office_from_id,
                       status_1, status_2, status_3, status_4,
                       status_5, status_6, status_7, status_8,
                       target_2h
                FROM route_status_history
                WHERE route_id = :route_id
                ORDER BY timestamp DESC
                LIMIT :limit
                """
            ),
            {"route_id": route_id, "limit": limit},
        )
        rows = result.mappings().all()

    if not rows:
        return pd.DataFrame(
            columns=[
                "timestamp", "route_id", "office_from_id",
                "status_1", "status_2", "status_3", "status_4",
                "status_5", "status_6", "status_7", "status_8",
                "target_2h",
            ]
        )

    df = pd.DataFrame([dict(r) for r in rows])
    # Reverse so oldest first (chronological order for feature computation)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


async def append_status_observation(
    route_id: int,
    warehouse_id: int,
    timestamp: str,
    statuses: dict[str, float],
) -> None:
    """Insert a new row into route_status_history."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO route_status_history
                    (route_id, office_from_id, timestamp,
                     status_1, status_2, status_3, status_4,
                     status_5, status_6, status_7, status_8)
                VALUES
                    (:route_id, :office_from_id, :timestamp,
                     :status_1, :status_2, :status_3, :status_4,
                     :status_5, :status_6, :status_7, :status_8)
                """
            ),
            {
                "route_id": route_id,
                "office_from_id": warehouse_id,
                "timestamp": timestamp,
                "status_1": statuses.get("status_1", 0.0),
                "status_2": statuses.get("status_2", 0.0),
                "status_3": statuses.get("status_3", 0.0),
                "status_4": statuses.get("status_4", 0.0),
                "status_5": statuses.get("status_5", 0.0),
                "status_6": statuses.get("status_6", 0.0),
                "status_7": statuses.get("status_7", 0.0),
                "status_8": statuses.get("status_8", 0.0),
            },
        )


async def check_connection() -> bool:
    """Check if the database is reachable."""
    try:
        engine = _get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database connection check failed")
        return False
