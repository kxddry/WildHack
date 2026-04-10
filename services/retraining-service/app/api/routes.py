"""FastAPI router for the retraining service."""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.orchestration import PromotionPolicy, run_retrain_cycle
from app.storage import postgres as db

logger = logging.getLogger(__name__)

router = APIRouter()
# Dashboard-facing read API mounted separately at /api/v1 so it never
# accidentally collides with the legacy /retrain/... surface.
router_v1 = APIRouter()

# In-memory store for the last retrain result (reset on restart)
_last_retrain_result: dict[str, Any] = {}
# Lock is shared between /retrain and /upload-dataset since both cycle the
# same ModelTrainer + ModelRegistry instances and a concurrent retrain from
# either entrypoint would race on the on-disk artifacts.
_retrain_lock = asyncio.Lock()


def get_retrain_lock() -> asyncio.Lock:
    """Accessor so upload.py can reuse the same singleton lock."""
    return _retrain_lock


def record_last_retrain_result(result: dict[str, Any]) -> None:
    """Allow other modules (upload.py) to update the /retrain/status cache."""
    global _last_retrain_result
    _last_retrain_result = result


def _get_trainer(request: Request):
    """Retrieve ModelTrainer from app state."""
    return request.app.state.trainer


def _get_registry(request: Request):
    """Retrieve ModelRegistry from app state."""
    return request.app.state.registry


def _normalise_model_entry(model: dict[str, Any], champion_version: str | None) -> dict[str, Any]:
    config = model.get("config_json") or {}
    return {
        **model,
        "config_json": config,
        "is_champion": model.get("model_version") == champion_version,
    }


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
    """Trigger a full retrain cycle using the shadow_if_better policy.

    Flow is orchestrated by ``app.core.orchestration.run_retrain_cycle``:
    fetch → build → train → evaluate → compare with champion → register
    → shadow-load if challenger is better.

    Training is CPU-bound and runs in a thread pool inside the orchestration
    helper to avoid blocking the event loop.
    """
    if _retrain_lock.locked():
        raise HTTPException(status_code=409, detail="Retrain already in progress")

    async with _retrain_lock:
        global _last_retrain_result

        trainer = _get_trainer(request)
        registry = _get_registry(request)
        started_at = datetime.utcnow().isoformat()

        try:
            outcome = await run_retrain_cycle(
                trainer,
                registry,
                policy=PromotionPolicy.SHADOW_IF_BETTER,
            )
        except ValueError as exc:
            finished_at = datetime.utcnow().isoformat()
            err: dict[str, Any] = {
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
                    new_model_version=None,
                    details=err,
                )
            except Exception:
                logger.exception("Failed to persist retrain history (failed run)")
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Retrain failed")
            finished_at = datetime.utcnow().isoformat()
            err = {
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
                    new_model_version=None,
                    details=err,
                )
            except Exception:
                logger.exception("Failed to persist retrain history (failed run)")
            raise HTTPException(
                status_code=500, detail="Retrain pipeline failed"
            ) from exc

        result = outcome.to_dict()
        _last_retrain_result = result
        return result


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
    models = await registry.get_all_versions()
    champion = await registry.get_champion()
    champion_version = champion.get("model_version") if champion else None
    return [
        _normalise_model_entry(model, champion_version)
        for model in models
    ]


@router.get("/models/champion")
async def get_champion(request: Request) -> dict:
    """Get the current champion model (lowest cv_score)."""
    registry = _get_registry(request)
    champion = await registry.get_champion()
    if champion is None:
        raise HTTPException(status_code=404, detail="No champion model registered yet")
    return _normalise_model_entry(champion, champion.get("model_version"))


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


# ---------------------------------------------------------------------------
# Dashboard-facing read APIs (mounted under /api/v1 by main.py)
# ---------------------------------------------------------------------------


@router_v1.get("/readiness/table-counts")
async def readiness_table_counts() -> dict[str, Any]:
    """Return live row counts for the tables the Readiness page tracks.

    Thin wrapper around ``db.get_table_counts`` — the read model already
    returns the exact shape the dashboard needs, so this endpoint just
    surfaces it through the BFF proxy without any reshaping.
    """
    counts = await db.get_table_counts()
    return counts


@router_v1.get("/models/registry")
async def registry_summary(request: Request) -> dict[str, Any]:
    """Return model registry entries plus champion and last retrain status."""
    registry = _get_registry(request)
    champion = await registry.get_champion()
    champion_version = champion.get("model_version") if champion else None
    models = await registry.get_all_versions()
    return {
        "models": [
            _normalise_model_entry(model, champion_version)
            for model in models
        ],
        "champion_version": champion_version,
        "last_retrain": _last_retrain_result,
    }
