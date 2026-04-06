"""Async and sync PostgreSQL storage for the retraining service."""

import json
import logging
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

_async_engine: AsyncEngine | None = None


# ---------------------------------------------------------------------------
# Async engine (used by API endpoints and registry operations)
# ---------------------------------------------------------------------------


async def create_engine_pool(database_url: str) -> AsyncEngine:
    """Create the async engine with connection pooling."""
    global _async_engine
    _async_engine = create_async_engine(
        database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )
    logger.info("Async database engine created for %s", database_url.split("@")[-1])
    return _async_engine


async def close_engine() -> None:
    """Dispose of the async engine and close all connections."""
    global _async_engine
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
        logger.info("Async database engine closed")


def _get_async_engine() -> AsyncEngine:
    if _async_engine is None:
        raise RuntimeError("Async database engine not initialized. Call create_engine_pool() first.")
    return _async_engine


async def check_connection() -> bool:
    """Check if the database is reachable."""
    try:
        engine = _get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database connection check failed")
        return False


# ---------------------------------------------------------------------------
# Sync engine (used by training data fetch — pandas.read_sql requires sync)
# ---------------------------------------------------------------------------


def create_sync_engine(sync_url: str):
    """Create a synchronous SQLAlchemy engine for training data fetches."""
    engine = create_engine(
        sync_url,
        pool_pre_ping=True,
        echo=False,
    )
    logger.info("Sync database engine created for %s", sync_url.split("@")[-1])
    return engine


def fetch_training_data(sync_url: str, window_days: int) -> pd.DataFrame:
    """Fetch training data from route_status_history for the last N days.

    Uses a synchronous engine so pandas.read_sql works without event loop issues.
    Returns a DataFrame sorted by (route_id, timestamp) ascending.
    """
    engine = create_sync_engine(sync_url)
    query = text("""
        SELECT
            timestamp,
            route_id,
            warehouse_id AS office_from_id,
            status_1, status_2, status_3, status_4,
            status_5, status_6, status_7, status_8,
            target_2h
        FROM route_status_history
        WHERE timestamp >= NOW() - MAKE_INTERVAL(days => :window_days)
          AND target_2h IS NOT NULL
        ORDER BY route_id, timestamp ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn, params={"window_days": window_days})

    engine.dispose()
    logger.info(
        "Fetched %d training rows over last %d days", len(df), window_days
    )
    return df


# ---------------------------------------------------------------------------
# Model metadata (async, used by ModelRegistry)
# ---------------------------------------------------------------------------


async def register_model(
    version: str,
    model_path: str,
    cv_score: float,
    feature_count: int,
    config: dict[str, Any],
) -> None:
    """Insert a new model version into model_metadata."""
    engine = _get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO model_metadata
                    (model_version, model_path, cv_score, training_date,
                     feature_count, config_json)
                VALUES
                    (:version, :model_path, :cv_score, :training_date,
                     :feature_count, :config_json)
                ON CONFLICT (model_version) DO UPDATE SET
                    model_path    = EXCLUDED.model_path,
                    cv_score      = EXCLUDED.cv_score,
                    training_date = EXCLUDED.training_date,
                    feature_count = EXCLUDED.feature_count,
                    config_json   = EXCLUDED.config_json
                """
            ),
            {
                "version": version,
                "model_path": model_path,
                "cv_score": cv_score,
                "training_date": datetime.utcnow(),
                "feature_count": feature_count,
                "config_json": json.dumps(config),
            },
        )
    logger.info("Registered model %s in model_metadata", version)


async def get_best_model() -> dict[str, Any] | None:
    """Return the model with the lowest cv_score (current champion)."""
    engine = _get_async_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, model_version, model_path, cv_score,
                       training_date, feature_count, config_json, created_at
                FROM model_metadata
                ORDER BY cv_score ASC
                LIMIT 1
                """
            )
        )
        row = result.mappings().first()
    if row is None:
        return None
    return dict(row)


async def get_all_models() -> list[dict[str, Any]]:
    """Return all model versions ordered by creation time descending."""
    engine = _get_async_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, model_version, model_path, cv_score,
                       training_date, feature_count, config_json, created_at
                FROM model_metadata
                ORDER BY created_at DESC
                """
            )
        )
        rows = result.mappings().all()
    return [dict(r) for r in rows]


async def get_recent_models(limit: int = 10) -> list[dict[str, Any]]:
    """Return recent model versions from model_metadata ordered by creation time descending."""
    engine = _get_async_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, model_version, model_path, cv_score,
                       training_date, feature_count, config_json, created_at
                FROM model_metadata
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        rows = result.mappings().all()
    return [dict(r) for r in rows]


async def save_retrain_history(
    started_at: str,
    completed_at: str,
    status: str,
    training_rows: int | None,
    champion_score: float | None,
    challenger_score: float | None,
    promoted: bool,
    new_model_version: str | None,
    details: dict,
) -> None:
    """Write a retrain run record to retrain_history table."""
    import json as _json
    engine = _get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO retrain_history
                    (started_at, completed_at, status, training_rows,
                     champion_score, challenger_score, promoted,
                     new_model_version, details_json)
                VALUES
                    (:started_at, :completed_at, :status, :training_rows,
                     :champion_score, :challenger_score, :promoted,
                     :new_model_version, :details_json)
                """
            ),
            {
                "started_at": datetime.fromisoformat(started_at) if isinstance(started_at, str) else started_at,
                "completed_at": datetime.fromisoformat(completed_at) if isinstance(completed_at, str) else completed_at,
                "status": status,
                "training_rows": training_rows,
                "champion_score": champion_score,
                "challenger_score": challenger_score,
                "promoted": promoted,
                "new_model_version": new_model_version,
                "details_json": _json.dumps(details, default=str),
            },
        )
