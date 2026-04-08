from __future__ import annotations

import logging
import time
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import (
    DispatchRequest,
    DispatchResponse,
    HealthResponse,
    ScheduleResponse,
    WarehouseItem,
    WarehouseListResponse,
)
from app.config import settings
from app.core.dispatcher import DispatchCalculator
from app.storage import postgres

logger = logging.getLogger(__name__)

router = APIRouter()

_start_time = time.monotonic()


@router.post("/dispatch", response_model=DispatchResponse)
async def create_dispatch(request: DispatchRequest) -> DispatchResponse:
    if request.forecasts is not None:
        # Caller passed explicit forecast steps. Build half-open slots
        # [ts, ts + step_interval) and drop any zero-length or negative
        # spans up-front so the DB never receives them (would violate the
        # dedup unique index anyway).
        slot_width = timedelta(minutes=int(settings.step_interval_minutes))
        forecasts = []
        for f in request.forecasts:
            slot_start = f.timestamp
            slot_end = slot_start + slot_width
            if slot_end <= slot_start:
                logger.warning(
                    "Skipping zero/negative-length slot at %s for warehouse %s",
                    slot_start,
                    request.warehouse_id,
                )
                continue
            forecasts.append(
                {
                    "time_slot_start": slot_start,
                    "time_slot_end": slot_end,
                    "total_containers": f.total_containers,
                }
            )
    elif request.time_range_start is not None and request.time_range_end is not None:
        forecasts = await postgres.get_recent_forecasts(
            warehouse_id=request.warehouse_id,
            time_start=request.time_range_start,
            time_end=request.time_range_end,
        )
        if not forecasts:
            raise HTTPException(
                status_code=404,
                detail=f"No forecasts found for warehouse {request.warehouse_id} "
                f"in the given time range",
            )
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'forecasts' or both 'time_range_start' and 'time_range_end'",
        )

    result = DispatchCalculator.create_full_dispatch(
        warehouse_id=request.warehouse_id,
        forecasts=forecasts,
        config=settings,
    )

    await postgres.save_transport_requests(result["dispatch_requests"])

    return DispatchResponse(
        warehouse_id=result["warehouse_id"],
        dispatch_requests=result["dispatch_requests"],
        config=result["config"],
    )


@router.get("/dispatch/schedule", response_model=ScheduleResponse)
async def get_schedule(
    warehouse_id: int = Query(..., description="Warehouse ID to get schedule for"),
) -> ScheduleResponse:
    schedule = await postgres.get_schedule(warehouse_id)
    return ScheduleResponse(warehouse_id=warehouse_id, schedule=schedule)


@router.get("/warehouses", response_model=WarehouseListResponse)
async def list_warehouses() -> WarehouseListResponse:
    rows = await postgres.get_all_warehouses()
    warehouses = [
        WarehouseItem(
            warehouse_id=row["warehouse_id"],
            name=row.get("name"),
            route_count=row["route_count"],
            latest_forecast_at=row.get("latest_forecast_at"),
            upcoming_trucks=row["upcoming_trucks"],
        )
        for row in rows
    ]
    return WarehouseListResponse(warehouses=warehouses, total=len(warehouses))


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    db_ok = await postgres.check_connection()
    elapsed = time.monotonic() - _start_time
    return HealthResponse(
        status="healthy" if db_ok else "degraded",
        database_connected=db_ok,
        uptime_seconds=round(elapsed, 2),
    )
