from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings
from app.core.dispatcher import DispatchCalculator

logger = logging.getLogger(__name__)

# PRD §9.2 — order accuracy tolerance: a slot counts as accurate when the
# predicted vehicle count is within this many trucks of the actual one.
ORDER_ACCURACY_TOLERANCE = 1

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


def _strip_tz(value):
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


async def _fetch_existing_trucks(
    conn,
    requests: list[dict],
) -> dict[tuple[int, datetime, datetime], int]:
    """Return mapping ``(warehouse_id, slot_start, slot_end) -> trucks_needed``
    for the exact slots referenced by ``requests``.

    Uses Postgres ``unnest`` over three parallel arrays so the join matches
    the input triples row-by-row instead of producing a Cartesian product
    of warehouse × start × end.
    """
    if not requests:
        return {}

    # Deduplicate by full triple to keep the unnest payload small while still
    # preserving exact pair semantics (start/end always travel together).
    unique_triples: dict[tuple[int, datetime, datetime], None] = {}
    for r in requests:
        key = (
            int(r["warehouse_id"]),
            _strip_tz(r["time_slot_start"]),
            _strip_tz(r["time_slot_end"]),
        )
        unique_triples.setdefault(key)

    warehouse_ids = [k[0] for k in unique_triples]
    starts = [k[1] for k in unique_triples]
    ends = [k[2] for k in unique_triples]

    result = await conn.execute(
        text(
            "SELECT tr.warehouse_id, tr.time_slot_start, tr.time_slot_end, "
            "tr.trucks_needed "
            "FROM transport_requests tr "
            "JOIN unnest("
            "    CAST(:warehouse_ids AS INTEGER[]), "
            "    CAST(:starts AS TIMESTAMP[]), "
            "    CAST(:ends   AS TIMESTAMP[])"
            ") AS slot(warehouse_id, time_slot_start, time_slot_end) "
            "  ON tr.warehouse_id    = slot.warehouse_id "
            " AND tr.time_slot_start = slot.time_slot_start "
            " AND tr.time_slot_end   = slot.time_slot_end"
        ),
        {
            "warehouse_ids": warehouse_ids,
            "starts": starts,
            "ends": ends,
        },
    )

    existing: dict[tuple[int, datetime, datetime], int] = {}
    for row in result.fetchall():
        mapping = row._mapping
        key = (
            int(mapping["warehouse_id"]),
            mapping["time_slot_start"],
            mapping["time_slot_end"],
        )
        existing[key] = int(mapping["trucks_needed"])
    return existing


async def save_transport_requests(requests: list[dict]) -> dict:
    """Persist dispatch decisions with antiflapping filter (PRD §7.3).

    Returns a small report dict with ``saved``/``skipped`` counters that callers
    (and tests) can inspect.  Existing rows whose ``trucks_needed`` differs from
    the new value by at most ``ANTIFLAP_DELTA_THRESHOLD`` are left untouched.

    Zero-length slots (``time_slot_end <= time_slot_start``) are refused up
    front — they break the ``UNIQUE (warehouse_id, time_slot_start,
    time_slot_end)`` contract (two distinct slots collide on the same key)
    and indicate upstream bugs in the forecast expansion path.
    """
    if not requests:
        return {"saved": 0, "skipped": 0, "total": 0}

    # Normalise timestamps once so both the SELECT and the lookup keys agree.
    normalised: list[dict] = []
    for r in requests:
        start = _strip_tz(r["time_slot_start"])
        end = _strip_tz(r["time_slot_end"])
        if end <= start:
            logger.warning(
                "Refusing zero/negative-length slot warehouse=%s start=%s end=%s",
                r.get("warehouse_id"),
                start,
                end,
            )
            continue
        normalised.append(
            {
                **r,
                "warehouse_id": int(r["warehouse_id"]),
                "time_slot_start": start,
                "time_slot_end": end,
            }
        )
    if not normalised:
        return {"saved": 0, "skipped": 0, "total": 0}

    engine = get_engine()
    async with engine.begin() as conn:
        existing = await _fetch_existing_trucks(conn, normalised)
        to_save, to_skip = DispatchCalculator.apply_antiflap_filter(
            normalised, existing
        )

        if to_skip:
            logger.info(
                "Antiflap filter skipped %d/%d dispatch updates",
                len(to_skip),
                len(normalised),
            )

        if to_save:
            await conn.execute(
                text(
                    "INSERT INTO transport_requests "
                    "(warehouse_id, time_slot_start, time_slot_end, "
                    "total_containers, truck_capacity, buffer_pct, "
                    "trucks_needed, calculation, status) "
                    "VALUES "
                    "(:warehouse_id, :time_slot_start, :time_slot_end, "
                    ":total_containers, :truck_capacity, :buffer_pct, "
                    ":trucks_needed, :calculation, :status) "
                    "ON CONFLICT (warehouse_id, time_slot_start, time_slot_end) "
                    "DO UPDATE SET "
                    "total_containers = EXCLUDED.total_containers, "
                    "truck_capacity = EXCLUDED.truck_capacity, "
                    "buffer_pct = EXCLUDED.buffer_pct, "
                    "trucks_needed = EXCLUDED.trucks_needed, "
                    "calculation = EXCLUDED.calculation, "
                    "updated_at = NOW()"
                ),
                [
                    {
                        "warehouse_id": r["warehouse_id"],
                        "time_slot_start": r["time_slot_start"],
                        "time_slot_end": r["time_slot_end"],
                        "total_containers": r["total_containers"],
                        "truck_capacity": r["truck_capacity"],
                        "buffer_pct": r["buffer_pct"],
                        "trucks_needed": r["trucks_needed"],
                        "calculation": r["calculation"],
                        "status": r["status"],
                    }
                    for r in to_save
                ],
            )

    return {
        "saved": len(to_save),
        "skipped": len(to_skip),
        "total": len(normalised),
    }


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
    """Fetch forecast steps overlapping ``[time_start, time_end)``.

    The forecasts table stores prediction horizons as a JSONB array on
    ``forecasts``. Each step has a ``timestamp`` (the horizon step's own
    clock time, NOT the anchor) and a ``predicted_value``.

    Previous behaviour — filtering by ``anchor_ts BETWEEN start AND end``
    and emitting zero-length slots where ``time_slot_end == time_slot_start``
    — was wrong in two distinct ways:

    1. ``anchor_ts`` is the *moment the forecast was computed*, not the
       *time the forecast is for*. A 5-hour-ahead forecast from 10:00 has
       anchor_ts=10:00 but covers steps from 10:30 → 15:00. Filtering by
       anchor_ts drops forecasts whose steps fall inside the requested
       window but whose anchor is outside it.
    2. Zero-length slots break ``transport_requests.UNIQUE(warehouse_id,
       time_slot_start, time_slot_end)`` contracts — downstream consumers
       can't tell slot A from slot B if they share both endpoints.

    New implementation:

    * Read every forecast row whose anchor is "close enough" to the window
      (anchor within ``[time_start - forecast_horizon, time_end]``) so we
      don't over-fetch but still capture rows whose later steps fall inside
      the window.
    * Expand each row's JSON steps into (step_ts, value) pairs.
    * Keep only steps whose timestamp overlaps ``[time_start, time_end)``
      (half-open).
    * Build slots with width ``settings.step_interval_minutes``, rejecting
      any slot whose end is not strictly greater than its start.
    """
    step_minutes = int(settings.step_interval_minutes)
    if step_minutes <= 0:
        raise ValueError(
            f"step_interval_minutes must be positive, got {step_minutes}"
        )
    slot_width = timedelta(minutes=step_minutes)

    # Strip tz for Postgres TIMESTAMP columns (stored as naive).
    window_start = _strip_tz(time_start)
    window_end = _strip_tz(time_end)

    # Over-fetch bound: anchor up to ``max_forecast_lookback`` minutes before
    # the window start so we don't miss rows whose late steps straddle the
    # requested range. 24h is a conservative cap — the longest forecast
    # horizon in the system today is 5h.
    anchor_floor = window_start - timedelta(hours=24)

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT route_id, anchor_ts, forecasts "
                "FROM forecasts "
                "WHERE warehouse_id = :warehouse_id "
                "AND anchor_ts >= :anchor_floor "
                "AND anchor_ts <  :window_end "
                "ORDER BY anchor_ts"
            ),
            {
                "warehouse_id": warehouse_id,
                "anchor_floor": anchor_floor,
                "window_end": window_end,
            },
        )
        rows = [dict(row._mapping) for row in result.fetchall()]

    items: list[dict] = []
    for row in rows:
        forecasts_data = row["forecasts"]
        if isinstance(forecasts_data, str):
            forecasts_data = json.loads(forecasts_data)
        if not isinstance(forecasts_data, list):
            continue
        for step in forecasts_data:
            ts_raw = step.get("timestamp") or step.get("ts")
            if ts_raw is None:
                continue
            pv = step.get("predicted_value")
            value = pv if pv is not None else step.get("value", 0.0)

            if isinstance(ts_raw, str):
                try:
                    step_ts = datetime.fromisoformat(ts_raw)
                except ValueError:
                    continue
            else:
                step_ts = ts_raw
            if hasattr(step_ts, "tzinfo") and step_ts.tzinfo:
                step_ts = step_ts.replace(tzinfo=None)

            # Half-open overlap: include any step whose slot [ts, ts+width)
            # intersects [window_start, window_end).
            slot_start = step_ts
            slot_end = slot_start + slot_width
            if slot_end <= window_start:
                continue
            if slot_start >= window_end:
                continue
            if slot_end <= slot_start:
                # Defensive: zero-length slots must never reach downstream.
                continue

            items.append(
                {
                    "time_slot_start": slot_start,
                    "time_slot_end": slot_end,
                    "total_containers": float(value),
                }
            )
    return items


async def get_all_warehouses() -> list[dict]:
    """Return all warehouses with dashboard-facing summary fields.

    Output columns: warehouse_id, name, route_count, latest_forecast_at,
    upcoming_trucks. Used by both the legacy /warehouses response model
    and the dashboard BFF's /api/warehouses proxy.
    """
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


async def list_recent_transport_requests(
    warehouse_id: int | None,
    status: str | None,
    limit: int,
) -> list[dict]:
    """Return recent transport_requests rows for the dashboard dispatch page.

    Filters:
      * ``warehouse_id`` — restrict to one warehouse if given.
      * ``status``      — restrict to a single status value if given.
      * ``limit``       — cap the result set (1..1000).

    Ordered by ``time_slot_start DESC`` so the "most recent decisions"
    tables on the dashboard light up the upcoming slots at the top.

    NOTE: this is distinct from PRD ``GET /api/v1/transport-requests``,
    which filters by an ``office_id`` + time window and joins forecasts
    to populate ``routes[]``. This endpoint is intentionally a simple
    row-listing read model so the dashboard can page recent decisions
    without pulling the PRD contract into UI territory.
    """
    conditions: list[str] = []
    params: dict = {"limit": int(limit)}
    if warehouse_id is not None:
        conditions.append("warehouse_id = :warehouse_id")
        params["warehouse_id"] = int(warehouse_id)
    if status:
        conditions.append("status = :status")
        params["status"] = status
    where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                f"SELECT id, warehouse_id, time_slot_start, time_slot_end, "
                f"total_containers, truck_capacity, buffer_pct, "
                f"trucks_needed, calculation, status, actual_vehicles, "
                f"actual_units, created_at, updated_at "
                f"FROM transport_requests{where_sql} "
                f"ORDER BY time_slot_start DESC "
                f"LIMIT :limit"
            ),
            params,
        )
        return [dict(row._mapping) for row in result.fetchall()]


async def get_transport_requests_window(
    office_id: int,
    range_from: datetime,
    range_to: datetime,
) -> list[dict]:
    """Return PRD §6.2 transport requests for ``office_id`` in ``[from, to]``.

    ``routes[]`` is populated by unrolling each forecast row's JSON steps
    (via ``jsonb_array_elements``) and joining by *step timestamp* overlap
    with the slot's window. The old implementation joined on
    ``f.anchor_ts`` which is the wall-clock time the forecast was computed
    — not the time the forecast actually covers. A 5-hour-ahead forecast
    from 10:00 has steps at 10:30, 11:00, … but an anchor_ts of 10:00, so
    the previous join missed every forecast whose anchor was outside the
    request's slot window.

    The step-level overlap rule matches the semantics of
    ``get_recent_forecasts``: a route contributes to a slot if any of its
    forecast steps fall inside ``[time_slot_start, time_slot_end)``.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                WITH step AS (
                    SELECT
                        f.route_id,
                        f.warehouse_id,
                        COALESCE(elem ->> 'timestamp', elem ->> 'ts')::timestamp AS step_ts
                    FROM forecasts f
                    CROSS JOIN LATERAL jsonb_array_elements(f.forecasts) AS elem
                    WHERE f.warehouse_id = :office_id
                )
                SELECT
                    tr.id,
                    tr.warehouse_id AS office_from_id,
                    tr.time_slot_start AS time_window_start,
                    tr.time_slot_end   AS time_window_end,
                    tr.total_containers AS total_predicted_units,
                    tr.trucks_needed   AS vehicles_required,
                    tr.status,
                    tr.created_at,
                    COALESCE(
                        ARRAY_AGG(DISTINCT s.route_id ORDER BY s.route_id)
                            FILTER (WHERE s.route_id IS NOT NULL),
                        ARRAY[]::INTEGER[]
                    ) AS routes
                FROM transport_requests tr
                LEFT JOIN step s
                  ON  s.warehouse_id = tr.warehouse_id
                  AND s.step_ts     >= tr.time_slot_start
                  AND s.step_ts     <  tr.time_slot_end
                WHERE tr.warehouse_id = :office_id
                  AND tr.time_slot_start >= :range_from
                  AND tr.time_slot_end   <= :range_to
                GROUP BY tr.id
                ORDER BY tr.time_slot_start
                """
            ),
            {
                "office_id": office_id,
                "range_from": _strip_tz(range_from),
                "range_to": _strip_tz(range_to),
            },
        )
        return [dict(row._mapping) for row in result.fetchall()]


async def get_business_metrics(
    range_from: datetime | None = None,
    range_to: datetime | None = None,
) -> dict:
    """Compute PRD §9.2 business KPIs over slots that have actual fulfilment.

    * ``order_accuracy``         — share of slots with |predicted - actual| ≤ 1
    * ``avg_truck_utilization``  — mean(actual_units / (vehicles * capacity))

    Slots without ``actual_vehicles`` are ignored. ``n_slots_total`` reflects
    every slot in the window so the dashboard can show the coverage ratio.
    """
    engine = get_engine()
    where_clauses = ["1 = 1"]
    params: dict = {}
    if range_from is not None:
        where_clauses.append("time_slot_start >= :range_from")
        params["range_from"] = _strip_tz(range_from)
    if range_to is not None:
        where_clauses.append("time_slot_end <= :range_to")
        params["range_to"] = _strip_tz(range_to)
    where_sql = " AND ".join(where_clauses)

    async with engine.connect() as conn:
        total_row = await conn.execute(
            text(f"SELECT COUNT(*) AS n FROM transport_requests WHERE {where_sql}"),
            params,
        )
        n_total = int(total_row.scalar_one() or 0)

        result = await conn.execute(
            text(
                "SELECT trucks_needed, actual_vehicles, actual_units, truck_capacity "
                "FROM transport_requests "
                f"WHERE {where_sql} AND actual_vehicles IS NOT NULL "
                "AND truck_capacity > 0"
            ),
            params,
        )
        rows = result.fetchall()

    if not rows:
        return {
            "order_accuracy": 0.0,
            "avg_truck_utilization": 0.0,
            "n_slots_evaluated": 0,
            "n_slots_total": n_total,
            "truck_capacity": 0,
        }

    accurate = 0
    utilizations: list[float] = []
    capacity_seen = 0
    for row in rows:
        m = row._mapping
        predicted = int(m["trucks_needed"])
        actual = int(m["actual_vehicles"])
        capacity = int(m["truck_capacity"])
        capacity_seen = capacity or capacity_seen
        if abs(predicted - actual) <= ORDER_ACCURACY_TOLERANCE:
            accurate += 1
        if actual > 0 and capacity > 0 and m["actual_units"] is not None:
            utilizations.append(float(m["actual_units"]) / float(actual * capacity))

    n_eval = len(rows)
    return {
        "order_accuracy": accurate / n_eval,
        "avg_truck_utilization": (sum(utilizations) / len(utilizations)) if utilizations else 0.0,
        "n_slots_evaluated": n_eval,
        "n_slots_total": n_total,
        "truck_capacity": capacity_seen,
    }


async def check_connection() -> bool:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("Database connection check failed", exc_info=True)
        return False
