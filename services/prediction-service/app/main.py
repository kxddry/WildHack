"""FastAPI application entrypoint for the WildHack Prediction Service."""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import router, router_v1
from app.config import settings
from app.core.model import ModelManager
from app.core.team_hybrid_featurizer import TeamHybridFeaturizer
from app.storage.postgres import close_engine, create_engine_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

model_manager = ModelManager()


def _check_required_artifacts() -> list[Path]:
    """Return paths of required model artifacts that are missing on disk.

    The service requires the model artifact to serve real predictions.
    Without it the predictor would either crash or — worse — silently emit
    synthetic outputs indistinguishable from real forecasts to downstream
    consumers (dispatcher, dashboard, Prometheus).
    Listing them up-front lets the lifespan handler decide between fail-fast
    and the explicit ``MOCK_MODE`` synthetic fallback.
    """
    return [
        Path(p)
        for p in (settings.model_path,)
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
            app.state.featurizer = None
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
        featurizer = TeamHybridFeaturizer(
            feat_cols_step=model_manager.feat_cols_step,
            feat_cols_global=model_manager.feat_cols_global,
            cat_cols=model_manager.cat_cols,
        )
        app.state.featurizer = featurizer

    await create_engine_pool(settings.database_url)

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

# Internal service — browsers never call this directly. Dashboard BFF proxy
# owns the only cross-origin surface. Leave the list empty so stray
# third-party origins are rejected.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=[],
    allow_headers=[],
)

app.include_router(router)
# Versioned API surface required by PRD §6 — exposed in addition to the
# legacy un-prefixed paths for backward compatibility with existing clients.
app.include_router(router, prefix="/api/v1")
# Dashboard-facing read APIs only live under /api/v1 — no legacy un-prefixed
# mirror since these endpoints are new and the dashboard calls them through
# the Next.js BFF proxy with a stable /api/v1/... path.
app.include_router(router_v1, prefix="/api/v1")

Instrumentator().instrument(app).expose(app)
