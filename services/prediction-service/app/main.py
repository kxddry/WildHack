"""FastAPI application entrypoint for the WildHack Prediction Service."""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import feature_engine, router
from app.config import settings
from app.core.model import ModelManager
from app.storage.postgres import close_engine, create_engine_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

model_manager = ModelManager()


def _check_required_artifacts() -> list[Path]:
    """Return paths of required model artifacts that are missing on disk.

    The service requires three artifacts to serve real predictions:
    1. ``model.pkl`` — the trained LightGBM model
    2. ``static_aggs.json`` — pre-computed aggregation tables for inference
    3. ``fill_values.json`` — median fill values from training

    Without any of these the predictor would either crash or — worse —
    silently emit synthetic outputs that are indistinguishable from real
    forecasts to downstream consumers (dispatcher, dashboard, Prometheus).
    Listing them up-front lets the lifespan handler decide between fail-fast
    and the explicit ``MOCK_MODE`` synthetic fallback.
    """
    return [
        Path(p)
        for p in (
            settings.model_path,
            settings.static_aggs_path,
            settings.fill_values_path,
        )
        if not Path(p).exists()
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load model and create DB pool. Shutdown: close DB pool."""
    # Startup
    logger.info("Starting prediction service...")

    missing = _check_required_artifacts()
    if missing:
        rendered = ", ".join(str(p) for p in missing)
        if settings.mock_mode:
            logger.warning(
                "MOCK_MODE=1 — enabling synthetic prediction mode. "
                "Missing artifacts: %s. DO NOT use this in production.",
                rendered,
            )
            model_manager.enable_mock_mode()
        else:
            # One clear message, then raise. FastAPI's lifespan will surface
            # the traceback; no need to log + raise the same string twice.
            raise FileNotFoundError(
                f"Missing required model artifacts: {rendered}. "
                "Place trained artifacts in the models/ directory, or set "
                "MOCK_MODE=1 to enable the local-dev synthetic fallback."
            )
    else:
        model_manager.load(settings.model_path)

    await create_engine_pool(settings.database_url)

    # When the real artifacts are present we always load them. When MOCK_MODE
    # is on and the artifacts are missing, we skip the loaders entirely —
    # they would both no-op with a warning, which is noisy. When MOCK_MODE is
    # on but the artifacts happen to exist, we still load them so the
    # synthetic predictor at least produces shapes that match reality.
    if not missing:
        feature_engine.load_static_aggregations(settings.static_aggs_path)
        feature_engine.load_fill_values(settings.fill_values_path)

    app.state.model_manager = model_manager
    app.state.startup_time = time.time()

    logger.info("Prediction service ready")
    yield

    # Shutdown
    logger.info("Shutting down prediction service...")
    await close_engine()


app = FastAPI(
    title="WildHack Prediction Service",
    description="Demand forecasting for automated transport dispatching",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
# Versioned API surface required by PRD §6 — exposed in addition to the
# legacy un-prefixed paths for backward compatibility with existing clients.
app.include_router(router, prefix="/api/v1")

Instrumentator().instrument(app).expose(app)
