"""FastAPI router for the retraining service."""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.storage import postgres as db

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory store for the last retrain result (reset on restart)
_last_retrain_result: dict[str, Any] = {}
_retrain_lock = asyncio.Lock()


def _get_trainer(request: Request):
    """Retrieve ModelTrainer from app state."""
    return request.app.state.trainer


def _get_registry(request: Request):
    """Retrieve ModelRegistry from app state."""
    return request.app.state.registry


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health")
async def health(request: Request) -> dict:
    """Service health check."""
    db_connected = await db.check_connection()
    startup_time: float = getattr(request.app.state, "startup_time", time.time())
    uptime = time.time() - startup_time
    status = "healthy" if db_connected else "degraded"
    return {
        "status": status,
        "database_connected": db_connected,
        "uptime_seconds": round(uptime, 2),
    }


# ---------------------------------------------------------------------------
# Retrain
# ---------------------------------------------------------------------------


@router.post("/retrain")
async def trigger_retrain(request: Request) -> dict:
    """Trigger a full retrain cycle.

    Flow: fetch data -> build features -> train -> evaluate ->
          compare with champion -> register -> (optionally) promote.
    Training is CPU-bound and runs in a thread pool to avoid blocking
    the event loop.
    """
    if _retrain_lock.locked():
        raise HTTPException(status_code=409, detail="Retrain already in progress")

    async with _retrain_lock:
        global _last_retrain_result

        trainer = _get_trainer(request)
        registry = _get_registry(request)

        version = f"v{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        started_at = datetime.utcnow().isoformat()

        try:
            from app.config import settings

            # Run CPU-bound training steps in a thread pool
            loop = asyncio.get_running_loop()

            logger.info("Retrain started — version %s", version)

            # 1. Fetch training data (sync, runs in thread)
            raw_df = await loop.run_in_executor(
                None, trainer.fetch_training_data, settings.training_window_days
            )

            # 2. Build features (sync, runs in thread)
            features_df = await loop.run_in_executor(None, trainer.build_features, raw_df)

            # 3. Train model (sync, runs in thread)
            model, metrics = await loop.run_in_executor(
                None, trainer.train_model, features_df
            )

            # 4. Save model to disk (sync, runs in thread)
            model_path = await loop.run_in_executor(
                None, trainer.save_model, model, version, metrics
            )

            # 4b. Recompute static aggregations and fill values from fresh training data
            try:
                await loop.run_in_executor(
                    None, trainer.save_static_aggs, raw_df, features_df, settings.model_output_dir
                )
            except Exception:
                logger.exception("Failed to save static aggs — training continues without update")

            # 5. Get current champion for comparison
            champion = await registry.get_champion()
            challenger_score = metrics["combined_score"]
            is_better = True  # promote by default when no champion exists

            if champion is not None:
                champion_score = champion.get("cv_score", float("inf"))
                is_better = trainer.compare_champion_challenger(champion_score, challenger_score)
                logger.info(
                    "Champion score=%.4f, challenger score=%.4f, is_better=%s",
                    champion_score, challenger_score, is_better,
                )

            # 6. Register challenger in model_metadata
            config = {
                "training_window_days": settings.training_window_days,
                "n_estimators": settings.n_estimators,
                "learning_rate": settings.learning_rate,
                "num_leaves": settings.num_leaves,
                "max_depth": settings.max_depth,
                "min_child_samples": settings.min_child_samples,
                "best_iteration": metrics.get("best_iteration"),
                "wape": metrics.get("wape"),
                "rbias": metrics.get("rbias"),
            }
            await registry.register_model(
                version=version,
                model_path=model_path,
                cv_score=challenger_score,
                feature_count=metrics.get("feature_count", 0),
                config=config,
            )

            # 7. Promote challenger to shadow for A/B testing if better than champion
            promotion_status = "skipped"
            if is_better:
                try:
                    await registry.promote_to_shadow(model_path)
                    promotion_status = "shadow_loaded"
                    logger.info("Challenger %s loaded as shadow model", version)
                except Exception:
                    logger.exception(
                        "Failed to load challenger %s as shadow — model registered but not promoted",
                        version,
                    )
                    promotion_status = "promotion_failed"

            result: dict[str, Any] = {
                "version": version,
                "model_path": model_path,
                "metrics": metrics,
                "is_better_than_champion": is_better,
                "promotion_status": promotion_status,
                "started_at": started_at,
                "finished_at": datetime.utcnow().isoformat(),
                "status": "success",
            }
            _last_retrain_result = result

            # Persist retrain history
            try:
                promoted = promotion_status == "shadow_loaded"
                await db.save_retrain_history(
                    started_at=started_at,
                    completed_at=result["finished_at"],
                    status="success",
                    training_rows=metrics.get("train_rows"),
                    champion_score=champion.get("cv_score") if champion else None,
                    challenger_score=challenger_score,
                    promoted=promoted,
                    new_model_version=version,
                    details=result,
                )
            except Exception:
                logger.exception("Failed to persist retrain history")

            return result

        except ValueError as exc:
            finished_at = datetime.utcnow().isoformat()
            err: dict[str, Any] = {
                "version": version,
                "status": "failed",
                "error": str(exc),
                "started_at": started_at,
                "finished_at": finished_at,
            }
            _last_retrain_result = err
            try:
                await db.save_retrain_history(
                    started_at=started_at,
                    completed_at=finished_at,
                    status="failed",
                    training_rows=None,
                    champion_score=None,
                    challenger_score=None,
                    promoted=False,
                    new_model_version=version,
                    details=err,
                )
            except Exception:
                logger.exception("Failed to persist retrain history (failed run)")
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Retrain failed for version %s", version)
            finished_at = datetime.utcnow().isoformat()
            err = {
                "version": version,
                "status": "failed",
                "error": str(exc),
                "started_at": started_at,
                "finished_at": finished_at,
            }
            _last_retrain_result = err
            try:
                await db.save_retrain_history(
                    started_at=started_at,
                    completed_at=finished_at,
                    status="failed",
                    training_rows=None,
                    champion_score=None,
                    challenger_score=None,
                    promoted=False,
                    new_model_version=version,
                    details=err,
                )
            except Exception:
                logger.exception("Failed to persist retrain history (failed run)")
            raise HTTPException(status_code=500, detail="Retrain pipeline failed") from exc


@router.get("/retrain/status")
async def retrain_status() -> dict:
    """Return the last retrain result (empty dict if no retrain has run yet)."""
    return _last_retrain_result


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


@router.get("/models")
async def list_models(request: Request) -> list[dict]:
    """List all registered model versions ordered by creation time."""
    registry = _get_registry(request)
    return await registry.get_all_versions()


@router.get("/models/champion")
async def get_champion(request: Request) -> dict:
    """Get the current champion model (lowest cv_score)."""
    registry = _get_registry(request)
    champion = await registry.get_champion()
    if champion is None:
        raise HTTPException(status_code=404, detail="No champion model registered yet")
    return champion


@router.post("/models/{version}/promote")
async def promote_version(version: str, request: Request) -> dict:
    """Promote a specific model version to primary in the prediction service.

    Loads it as shadow first, then promotes shadow to primary.
    """
    registry = _get_registry(request)

    # Look up the model path from the registry
    all_models = await registry.get_all_versions()
    match = next((m for m in all_models if m["model_version"] == version), None)
    if match is None:
        raise HTTPException(
            status_code=404, detail=f"Model version '{version}' not found"
        )

    model_path = match["model_path"]

    try:
        # Load as shadow first
        await registry.promote_to_shadow(model_path)
        # Then promote shadow to primary
        result = await registry.promote_to_primary(model_path)
        return {"version": version, "model_path": model_path, "result": result}
    except Exception as exc:
        logger.exception("Failed to promote version %s", version)
        raise HTTPException(
            status_code=500, detail=f"Promotion failed: {exc}"
        ) from exc


@router.post("/models/{version}/shadow")
async def load_shadow(version: str, request: Request) -> dict:
    """Load a specific model version as shadow in the prediction service."""
    registry = _get_registry(request)

    all_models = await registry.get_all_versions()
    match = next((m for m in all_models if m["model_version"] == version), None)
    if match is None:
        raise HTTPException(
            status_code=404, detail=f"Model version '{version}' not found"
        )

    model_path = match["model_path"]

    try:
        result = await registry.promote_to_shadow(model_path)
        return {"version": version, "model_path": model_path, "result": result}
    except Exception as exc:
        logger.exception("Failed to load shadow version %s", version)
        raise HTTPException(
            status_code=500, detail=f"Shadow load failed: {exc}"
        ) from exc
