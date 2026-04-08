"""FastAPI application entrypoint for the WildHack Prediction Service."""

import logging
import time
from contextlib import asynccontextmanager

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load model and create DB pool. Shutdown: close DB pool."""
    # Startup
    logger.info("Starting prediction service...")
    try:
        model_manager.load(settings.model_path)
    except FileNotFoundError:
        logger.warning("Model not found at %s — enabling mock prediction mode", settings.model_path)
        model_manager.enable_mock_mode()
    await create_engine_pool(settings.database_url)
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
