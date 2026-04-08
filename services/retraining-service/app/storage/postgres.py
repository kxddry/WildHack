"""Async and sync PostgreSQL storage for the retraining service."""

import json
import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

_async_engine: AsyncEngine | None = None


def _decode_json_like(value: Any) -> Any:
    """Return JSONB fields as native Python objects.

    asyncpg already maps JSONB to Python containers in most cases, but the
    tests and some local adapters surface raw strings. Normalising here keeps
    the API layer independent of driver quirks.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _decode_model_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise a model_metadata row for API consumers."""
    decoded = dict(row)
    decoded["config_json"] = _decode_json_like(decoded.get("config_json")) or {}
    return decoded


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


def _as_naive_timestamp(value: datetime | None) -> datetime | None:
    """Strip timezone info so values match Postgres TIMESTAMP columns."""
    if value is None:
        return None
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def fetch_training_data(
    sync_url: str,
    window_days: int,
    reference_ts: datetime | None = None,
) -> pd.DataFrame:
    """Fetch training data from route_status_history for the last N days.

    Uses a synchronous engine so pandas.read_sql works without event loop issues.
    Returns a DataFrame sorted by (route_id, timestamp) ascending.
    """
    engine = create_sync_engine(sync_url)
    anchor_ts = _as_naive_timestamp(reference_ts) or datetime.utcnow()
    query = text("""
        SELECT
            timestamp,
            route_id,
            warehouse_id AS office_from_id,
            status_1, status_2, status_3, status_4,
            status_5, status_6, status_7, status_8,
            target_2h
        FROM route_status_history
        WHERE timestamp >= :anchor_ts - MAKE_INTERVAL(days => :window_days)
          AND target_2h IS NOT NULL
        ORDER BY route_id, timestamp ASC
    """)
    with engine.connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params={
                "window_days": window_days,
                "anchor_ts": anchor_ts,
            },
        )

    engine.dispose()
    logger.info(
        "Fetched %d training rows over last %d days anchored to %s",
        len(df),
        window_days,
        anchor_ts.isoformat(),
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
    return _decode_model_row(dict(row))


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
    return [_decode_model_row(dict(r)) for r in rows]


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
    return [_decode_model_row(dict(r)) for r in rows]


async def get_model_by_version(version: str) -> dict[str, Any] | None:
    """Return a specific model version from model_metadata."""
    engine = _get_async_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT id, model_version, model_path, cv_score,
                       training_date, feature_count, config_json, created_at
                FROM model_metadata
                WHERE model_version = :version
                LIMIT 1
                """
            ),
            {"version": version},
        )
        row = result.mappings().first()
    if row is None:
        return None
    return _decode_model_row(dict(row))


# ---------------------------------------------------------------------------
# Dataset ingestion (used by /upload-dataset)
# ---------------------------------------------------------------------------
#
# All three tables (warehouses, routes, route_status_history) are populated
# inside a single transaction via `ingest_dataset`. The private helpers take
# an explicit connection so the caller owns the transaction boundary —
# otherwise a half-failed upload would leave partial warehouses+routes with
# no history, breaking the atomicity promised in the /upload-dataset
# docstring. Row counts are captured inside the same transaction so
# `rows_inserted` is not racy against concurrent writers.

_WAREHOUSE_UPSERT = text(
    """
    INSERT INTO warehouses (warehouse_id, route_count, first_seen, last_seen)
    VALUES (:warehouse_id, :route_count, :first_seen, :last_seen)
    ON CONFLICT (warehouse_id) DO UPDATE SET
        route_count = GREATEST(warehouses.route_count, EXCLUDED.route_count),
        first_seen  = LEAST(warehouses.first_seen, EXCLUDED.first_seen),
        last_seen   = GREATEST(warehouses.last_seen, EXCLUDED.last_seen)
    """
)

_ROUTE_INSERT = text(
    """
    INSERT INTO routes (route_id, warehouse_id)
    VALUES (:route_id, :warehouse_id)
    ON CONFLICT (route_id) DO NOTHING
    """
)

_HISTORY_INSERT = text(
    """
    INSERT INTO route_status_history
        (route_id, warehouse_id, timestamp,
         status_1, status_2, status_3, status_4,
         status_5, status_6, status_7, status_8, target_2h)
    VALUES
        (:route_id, :warehouse_id, :timestamp,
         :status_1, :status_2, :status_3, :status_4,
         :status_5, :status_6, :status_7, :status_8, :target_2h)
    ON CONFLICT (route_id, timestamp) DO NOTHING
    """
)


async def ingest_dataset(
    warehouses: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    history_chunks: Iterable[list[dict[str, Any]]],
) -> dict[str, int]:
    """Atomically persist a user-uploaded dataset.

    All three writes happen in a single transaction — if the history bulk
    insert fails halfway, the warehouses/routes upserts are rolled back so
    the operator never sees a split-brain state.

    `history_chunks` is an iterable of pre-chunked row lists so the caller
    can stream them from the DataFrame without materializing the whole
    dataset as Python dicts (important for 200 MB uploads).

    Returns a dict with the pre/post history row counts — safe to subtract
    because the snapshot is taken inside the same transaction as the writes.
    """
    engine = _get_async_engine()
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT COUNT(*) FROM route_status_history"))
        rows_before = int(result.scalar() or 0)

        if warehouses:
            await conn.execute(_WAREHOUSE_UPSERT, warehouses)
        if routes:
            await conn.execute(_ROUTE_INSERT, routes)

        rows_submitted = 0
        for chunk in history_chunks:
            if not chunk:
                continue
            await conn.execute(_HISTORY_INSERT, chunk)
            rows_submitted += len(chunk)

        result = await conn.execute(text("SELECT COUNT(*) FROM route_status_history"))
        rows_after = int(result.scalar() or 0)

    return {
        "rows_before": rows_before,
        "rows_after": rows_after,
        "rows_inserted": max(0, rows_after - rows_before),
        "rows_submitted": rows_submitted,
    }


async def refresh_snapshot(
    history_chunks: Iterable[list[dict[str, Any]]],
    retention_cutoff: datetime,
    retention_days: int,
    rows_received: int,
) -> dict[str, Any]:
    """Replace the live snapshot with the retained upload slice.

    Sequence (all inside ONE transaction — rollback on any failure):

    1. Clear ``forecasts`` and ``transport_requests`` — they were derived
       from the previous snapshot.
    2. Clear ``routes``, ``warehouses``, and ``route_status_history`` so the
       upload becomes authoritative instead of being merged with stale rows.
    3. Stream only the retained upload rows (already filtered by the caller's
       cutoff) into ``route_status_history`` via the standard idempotent
       insert path.
    4. Rebuild ``routes`` and ``warehouses`` aggregates from the retained
       slice so they exactly match what remains in history.

    Returns a summary dict for the upload response (retention cutoff,
    pre/post counts, cleared-table counts).
    """
    engine = _get_async_engine()
    retention_cutoff = _as_naive_timestamp(retention_cutoff)
    if retention_cutoff is None:
        raise ValueError("retention_cutoff must be provided for snapshot refresh")

    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT COUNT(*) FROM route_status_history"))
        rows_before = int(result.scalar() or 0)

        # 1. Clear derived tables first — they refer to the old snapshot.
        fc_result = await conn.execute(text("DELETE FROM forecasts"))
        tr_result = await conn.execute(text("DELETE FROM transport_requests"))
        cleared_forecasts = int(fc_result.rowcount or 0)
        cleared_transport_requests = int(tr_result.rowcount or 0)

        # 2. Clear live snapshot tables so the upload replaces them outright.
        await conn.execute(text("DELETE FROM routes"))
        await conn.execute(text("DELETE FROM warehouses"))
        await conn.execute(text("DELETE FROM route_status_history"))

        # 3. Insert only the retained upload rows.
        rows_submitted = 0
        for chunk in history_chunks:
            if not chunk:
                continue
            await conn.execute(_HISTORY_INSERT, chunk)
            rows_submitted += len(chunk)

        # 4. Rebuild routes + warehouses from the retained history slice.
        await conn.execute(
            text(
                """
                INSERT INTO warehouses (warehouse_id, route_count, first_seen, last_seen)
                SELECT
                    warehouse_id,
                    COUNT(DISTINCT route_id) AS route_count,
                    MIN(timestamp)           AS first_seen,
                    MAX(timestamp)           AS last_seen
                FROM route_status_history
                GROUP BY warehouse_id
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO routes (route_id, warehouse_id)
                SELECT DISTINCT ON (route_id) route_id, warehouse_id
                FROM route_status_history
                ORDER BY route_id, timestamp DESC
                """
            )
        )

        # Final counts — captured in the same transaction.
        routes_after = int(
            (await conn.execute(text("SELECT COUNT(*) FROM routes"))).scalar() or 0
        )
        warehouses_after = int(
            (
                await conn.execute(text("SELECT COUNT(*) FROM warehouses"))
            ).scalar()
            or 0
        )
        rows_after = int(
            (
                await conn.execute(
                    text("SELECT COUNT(*) FROM route_status_history")
                )
            ).scalar()
            or 0
        )

    return {
        "rows_before": rows_before,
        "rows_after": rows_after,
        "rows_inserted": rows_after,
        "rows_submitted": rows_submitted,
        "retention_cutoff": retention_cutoff.isoformat(),
        "retention_days": retention_days,
        "pruned_history_rows": rows_before + max(0, rows_received - rows_submitted),
        "cleared_forecasts": cleared_forecasts,
        "cleared_transport_requests": cleared_transport_requests,
        "routes_after": routes_after,
        "warehouses_after": warehouses_after,
    }


async def get_table_counts() -> dict[str, int]:
    """Return live row counts for every table the readiness page reports on.

    Used by the dashboard's /api/db/stats endpoint so the readiness dots
    reflect actual DB state, not fabricated derivations from the warehouses
    list.
    """
    engine = _get_async_engine()
    counts: dict[str, int] = {}
    async with engine.connect() as conn:
        for table in (
            "warehouses",
            "routes",
            "route_status_history",
            "forecasts",
            "transport_requests",
        ):
            # Table names are a fixed whitelist; no injection risk.
            result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            counts[table] = int(result.scalar() or 0)
    return counts


async def get_route_history_windows(
    route_ids: list[int],
    limit: int,
) -> list[dict[str, Any]]:
    """Return bounded chronological history windows for each requested route.

    ``limit`` is applied per-route via ``ROW_NUMBER() OVER (PARTITION BY ...)``.
    The final result is ordered by ``route_id, timestamp`` ascending so callers
    can feed the rows directly into the feature builder without an extra sort.
    """
    if not route_ids:
        return []

    engine = _get_async_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                WITH ranked AS (
                    SELECT
                        route_id,
                        warehouse_id AS office_from_id,
                        timestamp,
                        status_1, status_2, status_3, status_4,
                        status_5, status_6, status_7, status_8,
                        target_2h,
                        ROW_NUMBER() OVER (
                            PARTITION BY route_id
                            ORDER BY timestamp DESC
                        ) AS rn
                    FROM route_status_history
                    WHERE route_id = ANY(:route_ids)
                )
                SELECT
                    route_id,
                    office_from_id,
                    timestamp,
                    status_1, status_2, status_3, status_4,
                    status_5, status_6, status_7, status_8,
                    target_2h
                FROM ranked
                WHERE rn <= :limit
                ORDER BY route_id ASC, timestamp ASC
                """
            ),
            {"route_ids": route_ids, "limit": limit},
        )
        rows = result.mappings().all()
    return [dict(row) for row in rows]


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
