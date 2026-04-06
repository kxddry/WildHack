"""Async PostgreSQL storage using SQLAlchemy async + asyncpg."""

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


async def create_engine_pool(database_url: str) -> AsyncEngine:
    """Create the async engine with connection pooling."""
    global _engine
    _engine = create_async_engine(
        database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )
    logger.info("Database engine created for %s", database_url.split("@")[-1])
    return _engine


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


async def get_active_routes() -> list[dict[str, Any]]:
    """Return all routes as list of {route_id, warehouse_id}."""
    engine = _get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT route_id, warehouse_id FROM routes ORDER BY route_id")
        )
        rows = result.mappings().all()
    return [dict(r) for r in rows]


async def get_latest_statuses(route_ids: list[int]) -> list[dict[str, Any]]:
    """Return the most recent status row per route from route_status_history.

    Uses DISTINCT ON to get one row per route_id, ordered by timestamp DESC.
    """
    if not route_ids:
        return []
    engine = _get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT DISTINCT ON (route_id)
                    route_id, warehouse_id, timestamp,
                    status_1, status_2, status_3, status_4,
                    status_5, status_6, status_7, status_8
                FROM route_status_history
                WHERE route_id = ANY(:route_ids)
                ORDER BY route_id, timestamp DESC
                """
            ),
            {"route_ids": route_ids},
        )
        rows = result.mappings().all()
    return [dict(r) for r in rows]


async def get_distinct_warehouses() -> list[int]:
    """Return distinct warehouse_ids from the routes table."""
    engine = _get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT DISTINCT warehouse_id FROM routes ORDER BY warehouse_id")
        )
        rows = result.fetchall()
    return [int(r[0]) for r in rows]


async def get_forecast_actual_pairs(since: datetime) -> list[dict[str, Any]]:
    """Join forecasts with route_status_history to compare predicted vs actual.

    Matches forecast rows to actual observations within a 5-minute window.
    Returns list of {route_id, predicted, actual, forecast_ts}.
    """
    engine = _get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                WITH expanded AS (
                    SELECT f.route_id, f.anchor_ts, f.model_version,
                           (elem->>'predicted_value')::double precision AS predicted,
                           (elem->>'timestamp')::timestamp AS step_ts
                    FROM forecasts f,
                         jsonb_array_elements(f.forecasts) AS elem
                    WHERE f.created_at >= :since
                )
                SELECT e.route_id, e.predicted, h.target_2h AS actual,
                       e.anchor_ts AS forecast_ts, e.model_version
                FROM expanded e
                JOIN route_status_history h
                    ON h.route_id = e.route_id
                    AND ABS(EXTRACT(EPOCH FROM (h.timestamp - e.step_ts))) <= 300
                WHERE h.target_2h IS NOT NULL
                ORDER BY e.anchor_ts DESC
                LIMIT 10000
                """
            ),
            {"since": since.replace(tzinfo=None) if since.tzinfo else since},
        )
        rows = result.mappings().all()
    return [dict(r) for r in rows]


async def get_pipeline_runs(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent pipeline run records from pipeline_runs table."""
    engine = _get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, run_type, status, started_at, completed_at, details
                FROM pipeline_runs
                ORDER BY started_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        rows = result.mappings().all()
    return [dict(r) for r in rows]


async def save_pipeline_run(run_data: dict[str, Any]) -> None:
    """Insert a pipeline run record into pipeline_runs table."""
    engine = _get_engine()

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO pipeline_runs (run_type, status, started_at, completed_at, details)
                VALUES (:run_type, :status, :started_at, :completed_at, :details)
                """
            ),
            {
                "run_type": run_data.get("run_type", "prediction_cycle"),
                "status": run_data.get("status", "unknown"),
                "started_at": run_data.get("started_at"),
                "completed_at": run_data.get("completed_at"),
                "details": json.dumps(run_data, default=str),
            },
        )


async def backfill_target_2h() -> int:
    """Set target_2h for rows where 2h have passed but label is still NULL.

    Returns number of rows updated.
    """
    engine = _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE route_status_history t
                SET target_2h = sub.val
                FROM (
                    SELECT t2.id,
                           (SELECT (f.status_1 + f.status_2 + f.status_3 + f.status_4 +
                                    f.status_5 + f.status_6 + f.status_7 + f.status_8)
                            FROM route_status_history f
                            WHERE f.route_id = t2.route_id
                              AND ABS(EXTRACT(EPOCH FROM (f.timestamp - (t2.timestamp + INTERVAL '2 hours')))) <= 300
                            ORDER BY ABS(EXTRACT(EPOCH FROM (f.timestamp - (t2.timestamp + INTERVAL '2 hours')))) ASC
                            LIMIT 1
                           ) AS val
                    FROM route_status_history t2
                    WHERE t2.target_2h IS NULL
                      AND t2.timestamp <= NOW() - INTERVAL '2 hours'
                ) sub
                WHERE t.id = sub.id AND sub.val IS NOT NULL
                """
            )
        )
    return result.rowcount


async def save_quality_check(metrics: dict) -> None:
    """Persist quality check result to prediction_quality table."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO prediction_quality
                    (checked_at, wape, rbias, combined_score, n_pairs, alert_triggered, details_json)
                VALUES
                    (:checked_at, :wape, :rbias, :combined_score, :n_pairs, :alert_triggered, :details_json)
                """
            ),
            {
                "checked_at": datetime.fromisoformat(metrics["checked_at"]) if isinstance(metrics.get("checked_at"), str) else metrics.get("checked_at"),
                "wape": metrics.get("wape"),
                "rbias": metrics.get("rbias"),
                "combined_score": metrics.get("combined_score"),
                "n_pairs": metrics.get("n_pairs", 0),
                "alert_triggered": metrics.get("alert_triggered", False),
                "details_json": json.dumps(metrics, default=str),
            },
        )
