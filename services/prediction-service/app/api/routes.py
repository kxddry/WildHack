"""FastAPI router with prediction, health, and model info endpoints."""

import logging
import time
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import (
    BatchPredictRequest,
    BatchPredictResponse,
    ForecastStep,
    HealthResponse,
    ModelInfoResponse,
    PredictRequest,
    PredictResponse,
)
from app.config import settings
from app.core.feature_engine import InferenceFeatureEngine
from app.storage import postgres
from app.storage.status_history import StatusHistoryManager

logger = logging.getLogger(__name__)

router = APIRouter()

status_history = StatusHistoryManager()
feature_engine = InferenceFeatureEngine()


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
    history_df = await status_history.get_route_history(
        req.route_id, limit=settings.history_window,
    )

    # Determine warehouse_id from history or current observation
    if not history_df.empty:
        warehouse_id = int(history_df["office_from_id"].iloc[-1])
    else:
        # First observation for this route -- look up warehouse
        try:
            warehouse_id = await status_history.get_warehouse_for_route(req.route_id)
        except ValueError:
            # No history at all; use route_id as a fallback warehouse_id
            # (will be corrected once real data flows in)
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
    await status_history.append_observation(
        route_id=req.route_id,
        warehouse_id=warehouse_id,
        timestamp=req.timestamp,
        statuses=statuses,
    )

    # 3. Build the history DataFrame including the new observation
    #    Append current row to the history for feature computation
    current_row = {
        "timestamp": pd.Timestamp(req.timestamp),
        "route_id": req.route_id,
        "office_from_id": warehouse_id,
        "target_2h": 0.0,  # Unknown at prediction time
        **statuses,
    }
    if history_df.empty:
        full_history = pd.DataFrame([current_row])
    else:
        new_row_df = pd.DataFrame([current_row])
        full_history = pd.concat([history_df, new_row_df], ignore_index=True)

    # 4. Build features via InferenceFeatureEngine
    features_df = feature_engine.build_features(
        history_df=full_history,
        route_id=req.route_id,
        warehouse_id=warehouse_id,
        forecast_steps=settings.forecast_steps,
    )

    # 5. Run model prediction
    predictions = model_manager.predict(features_df)

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
    forecasts_json = [fs.model_dump(mode="json") for fs in forecast_steps]
    try:
        await postgres.save_forecasts(
            route_id=req.route_id,
            warehouse_id=warehouse_id,
            anchor_ts=anchor_ts.isoformat(),
            forecasts_json=forecasts_json,
            model_version=settings.model_version,
        )
    except Exception:
        logger.exception("Failed to save forecasts for route_id=%d", req.route_id)
        # Non-fatal: return predictions even if storage fails

    return PredictResponse(
        route_id=req.route_id,
        warehouse_id=warehouse_id,
        anchor_timestamp=anchor_ts,
        forecasts=forecast_steps,
        model_version=settings.model_version,
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
    results: list[PredictResponse] = []

    for pred_req in req.predictions:
        try:
            result = await _run_single_prediction(request, pred_req)
            results.append(result)
        except Exception:
            logger.exception("Batch prediction failed for route_id=%d", pred_req.route_id)
            # Skip failed routes in batch mode, continue with the rest

    elapsed_ms = (time.monotonic() - start) * 1000.0

    return BatchPredictResponse(
        results=results,
        total=len(results),
        processing_time_ms=round(elapsed_ms, 2),
    )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Health check endpoint."""
    model_manager = request.app.state.model_manager
    model_loaded = model_manager.is_loaded
    db_connected = await postgres.check_connection()

    startup_time: float = getattr(request.app.state, "startup_time", time.time())
    uptime = time.time() - startup_time

    status = "healthy" if (model_loaded and db_connected) else "degraded"

    return HealthResponse(
        status=status,
        model_loaded=model_loaded,
        database_connected=db_connected,
        uptime_seconds=round(uptime, 2),
    )


@router.get("/model/info", response_model=ModelInfoResponse)
async def model_info(request: Request) -> ModelInfoResponse:
    """Return model metadata and introspected properties."""
    model_manager = _get_model_manager(request)
    info = model_manager.info()

    return ModelInfoResponse(
        model_version=settings.model_version,
        model_type=info.get("model_type", "unknown"),
        objective=info.get("objective", "unknown"),
        cv_score=info.get("cv_score"),
        feature_count=info.get("feature_count", 0),
        training_date=info.get("training_date"),
        forecast_horizon=settings.forecast_steps,
        step_interval_minutes=settings.step_interval_minutes,
    )
