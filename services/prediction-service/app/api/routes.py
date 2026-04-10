"""FastAPI router with prediction, health, and model info endpoints."""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.schemas import (
    BatchPredictRequest,
    BatchPredictResponse,
    ForecastStep,
    HealthResponse,
    ModelInfoResponse,
    PredictRequest,
    PredictResponse,
)
from app.api.security import require_internal_token
from app.config import settings
from app.storage import postgres

logger = logging.getLogger(__name__)

router = APIRouter()
# Dedicated router for the dashboard-facing read APIs. Kept separate from
# ``router`` so main.py can mount it exactly once under ``/api/v1`` without
# the double-mount quirk that the legacy router has (re-mounted under
# ``/api/v1`` for backward compatibility). Prevents paths like
# ``/api/v1/api/v1/forecasts``.
router_v1 = APIRouter()

COLD_START_THRESHOLD = 24  # minimum 12 hours of history needed


def _get_model_manager(request: Request):
    """Retrieve ModelManager from app state, avoiding circular imports."""
    model_manager = request.app.state.model_manager
    if not model_manager.is_loaded:
        raise HTTPException(status_code=503, detail="Model is not loaded")
    return model_manager


async def _run_single_prediction(request: Request, req: PredictRequest) -> PredictResponse:
    """Execute the full prediction pipeline for a single route."""
    model_manager = _get_model_manager(request)

    # 1. Get route history
    history_df = await postgres.get_route_status_history(
        req.route_id, limit=settings.history_window,
    )

    # Determine warehouse_id from routes table or history fallback
    if not history_df.empty:
        warehouse_id = int(history_df["office_from_id"].iloc[-1])
    else:
        try:
            warehouse_id = await postgres.get_warehouse_for_route(req.route_id)
        except ValueError:
            logger.warning("No warehouse mapping for route_id=%d, using route_id as fallback", req.route_id)
            warehouse_id = req.route_id

    # 2. Append current observation to history
    statuses = {
        "status_1": req.status_1,
        "status_2": req.status_2,
        "status_3": req.status_3,
        "status_4": req.status_4,
        "status_5": req.status_5,
        "status_6": req.status_6,
        "status_7": req.status_7,
        "status_8": req.status_8,
    }
    await postgres.append_status_observation(
        route_id=req.route_id,
        warehouse_id=warehouse_id,
        timestamp=req.timestamp,
        statuses=statuses,
    )

    # 3. Build the history DataFrame including the new observation
    current_row = {
        "timestamp": pd.Timestamp(req.timestamp).tz_localize(None),
        "route_id": req.route_id,
        "office_from_id": warehouse_id,
        "target_2h": 0.0,  # Unknown at prediction time
        **statuses,
    }

    cold_start = len(history_df) < COLD_START_THRESHOLD
    if cold_start and warehouse_id:
        logger.warning(
            "Cold-start for route_id=%d (only %d rows), using warehouse average",
            req.route_id,
            len(history_df),
        )
        fallback_df = await postgres.get_warehouse_avg_history(warehouse_id, limit=settings.history_window)
        if not fallback_df.empty:
            fallback_df = fallback_df.copy()
            fallback_df["route_id"] = req.route_id
            fallback_df["office_from_id"] = warehouse_id
            new_row_df = pd.DataFrame([current_row])
            full_history = pd.concat([fallback_df, new_row_df], ignore_index=True)
        elif history_df.empty:
            full_history = pd.DataFrame([current_row])
        else:
            new_row_df = pd.DataFrame([current_row])
            full_history = pd.concat([history_df, new_row_df], ignore_index=True)
    elif history_df.empty:
        full_history = pd.DataFrame([current_row])
    else:
        new_row_df = pd.DataFrame([current_row])
        full_history = pd.concat([history_df, new_row_df], ignore_index=True)

    # 4. Build features via TeamHybridFeaturizer (skipped in mock mode)
    featurizer = request.app.state.featurizer
    if featurizer is not None:
        features_df = featurizer.build(
            history_df=full_history,
            anchor_ts=req.timestamp,
            route_id=req.route_id,
            warehouse_id=warehouse_id,
            forecast_steps=settings.forecast_steps,
        )
    else:
        # Mock mode — build a minimal DataFrame for _mock_predict
        features_df = full_history.tail(1).copy()

    # 5a. Run primary model prediction
    predictions = model_manager.predict(features_df)

    # 5b. Shadow model prediction (non-blocking, for A/B comparison)
    shadow_steps = None
    shadow_preds = model_manager.predict_shadow(features_df)
    if shadow_preds is not None:
        shadow_steps = []
        for i, pred_value in enumerate(shadow_preds):
            step_num = i + 1
            step_ts = req.timestamp + pd.Timedelta(minutes=settings.step_interval_minutes * step_num)
            shadow_steps.append(
                ForecastStep(
                    horizon_step=step_num,
                    timestamp=step_ts,
                    predicted_value=round(float(pred_value), 4),
                )
            )

    # 5c. Save shadow forecasts to DB
    if shadow_steps is not None:
        shadow_version = model_manager.shadow_version or "shadow"
        shadow_json = [fs.model_dump(mode="json") for fs in shadow_steps]
        try:
            await postgres.save_forecasts(
                route_id=req.route_id,
                warehouse_id=warehouse_id,
                anchor_ts=req.timestamp,
                forecasts_json=shadow_json,
                model_version=shadow_version,
            )
        except Exception:
            logger.warning("Failed to save shadow forecasts for route_id=%d", req.route_id)

    # 6. Build forecast steps
    anchor_ts = req.timestamp
    forecast_steps = []
    for i, pred_value in enumerate(predictions):
        step_num = i + 1
        step_ts = anchor_ts + pd.Timedelta(minutes=settings.step_interval_minutes * step_num)
        forecast_steps.append(
            ForecastStep(
                horizon_step=step_num,
                timestamp=step_ts,
                predicted_value=round(float(pred_value), 4),
            )
        )

    # 7. Save forecasts to PostgreSQL
    #
    # Source of truth for the active model version is ModelManager.runtime_version
    # (metadata.model_version → artifact stem → legacy setting). This ensures a
    # freshly-promoted model is reflected in both /model/info and the forecasts
    # rows written here, with no dependency on the static config label.
    runtime_version = model_manager.runtime_version
    forecasts_json = [fs.model_dump(mode="json") for fs in forecast_steps]
    try:
        await postgres.save_forecasts(
            route_id=req.route_id,
            warehouse_id=warehouse_id,
            anchor_ts=anchor_ts,
            forecasts_json=forecasts_json,
            model_version=runtime_version,
        )
    except Exception:
        logger.exception("Failed to save forecasts for route_id=%d", req.route_id)
        # Non-fatal: return predictions even if storage fails

    return PredictResponse(
        route_id=req.route_id,
        warehouse_id=warehouse_id,
        anchor_timestamp=anchor_ts,
        forecasts=forecast_steps,
        model_version=runtime_version,
        shadow_forecasts=shadow_steps,
    )


@router.post("/predict", response_model=PredictResponse)
async def predict(request: Request, req: PredictRequest) -> PredictResponse:
    """Run prediction pipeline for a single route."""
    try:
        return await _run_single_prediction(request, req)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Prediction failed for route_id=%d", req.route_id)
        raise HTTPException(status_code=500, detail="Prediction pipeline failed")


@router.post("/predict/batch", response_model=BatchPredictResponse)
async def predict_batch(request: Request, req: BatchPredictRequest) -> BatchPredictResponse:
    """Run prediction pipeline for multiple routes."""
    start = time.monotonic()
    semaphore = asyncio.Semaphore(10)

    async def _predict_with_limit(pred_req: PredictRequest) -> PredictResponse:
        async with semaphore:
            return await _run_single_prediction(request, pred_req)

    tasks = [_predict_with_limit(r) for r in req.predictions]
    settled = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for i, result in enumerate(settled):
        if isinstance(result, Exception):
            logger.exception("Batch prediction failed for route_id=%d", req.predictions[i].route_id)
        else:
            results.append(result)

    elapsed_ms = (time.monotonic() - start) * 1000.0
    return BatchPredictResponse(results=results, total=len(results), processing_time_ms=round(elapsed_ms, 2))


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Health check endpoint."""
    model_manager = request.app.state.model_manager
    model_loaded = model_manager.is_loaded
    db_connected = await postgres.check_connection()

    startup_time: float = getattr(request.app.state, "startup_time", time.time())
    uptime = time.time() - startup_time

    is_mock = model_manager.is_mock
    if model_loaded and db_connected:
        status = "mock" if is_mock else "healthy"
    else:
        status = "degraded"

    return HealthResponse(
        status=status,
        model_loaded=model_loaded,
        database_connected=db_connected,
        uptime_seconds=round(uptime, 2),
    )


@router.get("/model/info", response_model=ModelInfoResponse)
async def model_info(request: Request) -> ModelInfoResponse:
    """Return model metadata and introspected properties.

    Reports the runtime model version — i.e. what the model manager will
    actually use to predict right now — not the static config label. This
    closes the split-brain where /model/info could report an older version
    than the forecasts being written to Postgres after a promote.
    """
    model_manager = _get_model_manager(request)
    info = model_manager.info()

    return ModelInfoResponse(
        model_version=info.get("model_version") or model_manager.runtime_version,
        model_type=info.get("model_type", "unknown"),
        objective=info.get("objective", "unknown"),
        cv_score=info.get("cv_score"),
        feature_count=info.get("feature_count", 0),
        training_date=info.get("training_date"),
        forecast_horizon=settings.forecast_steps,
        step_interval_minutes=settings.step_interval_minutes,
    )


@router.post(
    "/model/reload",
    dependencies=[Depends(require_internal_token)],
)
async def reload_model(request: Request) -> dict:
    """Hot-reload the model from disk without restarting the service.

    Protected with X-Internal-Token — reloading re-reads the canonical
    model.pkl + model_metadata.json pair, so it must not be reachable from
    random pages on the LAN.
    """
    model_manager = request.app.state.model_manager
    try:
        result = model_manager.reload(settings.model_path)
        return {"status": "reloaded", "details": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model reload failed: {e}")


@router.post(
    "/model/shadow/load",
    dependencies=[Depends(require_internal_token)],
)
async def load_shadow_model(
    request: Request,
    path: str = Query("models/shadow_model.pkl"),
) -> dict:
    """Load a shadow/challenger model for A/B comparison."""
    from pathlib import Path

    model_manager = request.app.state.model_manager

    # Validate path is within allowed model directory
    resolved = Path(path).resolve()
    allowed_dir = Path(settings.model_path).resolve().parent if settings.model_path else Path("/app/models").resolve()
    if not resolved.is_relative_to(allowed_dir):
        raise HTTPException(status_code=400, detail="Model path must be within the models directory")

    try:
        model_manager.load_shadow(path)
        return {"status": "shadow_loaded", "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/model/shadow/promote",
    dependencies=[Depends(require_internal_token)],
)
async def promote_shadow(request: Request) -> dict:
    """Promote shadow model to primary."""
    model_manager = request.app.state.model_manager
    if not model_manager.has_shadow:
        raise HTTPException(status_code=404, detail="No shadow model loaded")
    model_manager.promote_shadow()
    return {
        "status": "promoted",
        "new_primary_path": model_manager._model_path,
        "runtime_version": model_manager.runtime_version,
    }


@router.delete(
    "/model/shadow",
    dependencies=[Depends(require_internal_token)],
)
async def remove_shadow(request: Request) -> dict:
    """Remove the loaded shadow model."""
    model_manager = request.app.state.model_manager
    model_manager.remove_shadow()
    return {"status": "shadow_removed"}


# ---------------------------------------------------------------------------
# Dashboard-facing read APIs (proxied through the Next.js BFF)
# ---------------------------------------------------------------------------
#
# These replace the dashboard's direct Postgres queries. The read surface is
# intentionally minimal — just what the forecast/dispatch/quality pages need
# — and normalises JSON shapes so the dashboard never has to handle multiple
# legacy forecast representations.


def _normalise_forecast_steps(raw: Any) -> list[dict[str, Any]]:
    """Convert legacy forecast step shapes to the canonical schema.

    Old writers emitted ``{ts, step, value}``; the current schema is
    ``{timestamp, horizon_step, predicted_value}``. Normalising here keeps
    the dashboard ignorant of the legacy variant. Unknown fields are
    dropped on purpose — the dashboard only cares about those three.
    """
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for step in raw:
        if not isinstance(step, dict):
            continue
        timestamp = step.get("timestamp") or step.get("ts") or ""
        horizon_step = step.get("horizon_step") or step.get("step") or 0
        predicted = step.get("predicted_value")
        if predicted is None:
            predicted = step.get("value", 0.0)
        out.append(
            {
                "horizon_step": int(horizon_step or 0),
                "timestamp": str(timestamp),
                "predicted_value": float(predicted or 0.0),
            }
        )
    return out


@router_v1.get("/forecasts")
async def list_forecasts(
    warehouse_id: int = Query(..., ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """List recent forecasts for a warehouse.

    Response envelope is ``{"forecasts": [...]}`` so future metadata
    fields (pagination cursor, model_version breakdown) can ride along
    without breaking existing clients.
    """
    rows = await postgres.list_forecasts_for_warehouse(
        warehouse_id=warehouse_id, limit=limit
    )
    forecasts = [
        {
            "id": row["id"],
            "route_id": row["route_id"],
            "warehouse_id": row["warehouse_id"],
            "anchor_ts": row["anchor_ts"].isoformat()
            if row["anchor_ts"] is not None
            else None,
            "forecasts": _normalise_forecast_steps(row["forecasts"]),
            "model_version": row["model_version"],
            "created_at": row["created_at"].isoformat()
            if row["created_at"] is not None
            else None,
        }
        for row in rows
    ]
    return {"forecasts": forecasts}


@router_v1.get("/routes/{route_id}/status-history")
async def list_status_history(
    route_id: int,
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Return the last ``limit`` status observations for a route.

    Rows are returned ascending by timestamp so the dashboard chart can
    render the series left-to-right without a client-side reverse.
    """
    rows = await postgres.list_route_status_history(
        route_id=route_id, limit=limit
    )
    history = [
        {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in row.items()
        }
        for row in rows
    ]
    return {"history": history}
