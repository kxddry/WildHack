"""Scheduler service API: pipeline status, manual triggers, quality reports."""

import logging
import time

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()

_start_time = time.monotonic()


@router.get("/health")
async def health(request: Request) -> dict:
    db_ok = await request.app.state.db.check_connection()
    elapsed = time.monotonic() - _start_time
    return {
        "status": "healthy" if db_ok else "degraded",
        "database_connected": db_ok,
        "uptime_seconds": round(elapsed, 2),
    }


@router.get("/pipeline/status")
async def pipeline_status(request: Request) -> dict:
    """Get current pipeline and quality checker status."""
    orchestrator = request.app.state.orchestrator
    quality = request.app.state.quality_checker
    return {
        "pipeline": orchestrator.status,
        "quality": quality.status,
    }


@router.post("/pipeline/trigger")
async def trigger_pipeline(request: Request) -> dict:
    """Manually trigger a prediction+dispatch cycle."""
    orchestrator = request.app.state.orchestrator
    db = request.app.state.db
    result = await orchestrator.run_prediction_cycle(from_db=db)
    return result


@router.post("/quality/trigger")
async def trigger_quality_check(request: Request) -> dict:
    """Manually trigger a quality evaluation."""
    quality = request.app.state.quality_checker
    db = request.app.state.db
    result = await quality.run_quality_check(from_db=db)
    return result


@router.get("/quality/alerts")
async def get_alerts(request: Request) -> dict:
    """Get active quality alerts."""
    quality = request.app.state.quality_checker
    return {"alerts": quality._alerts, "last_metrics": quality._last_metrics}


@router.get("/pipeline/history")
async def pipeline_history(request: Request, limit: int = 20) -> dict:
    """Get recent pipeline run history."""
    db = request.app.state.db
    runs = await db.get_pipeline_runs(limit=limit)
    return {"runs": runs, "total": len(runs)}
