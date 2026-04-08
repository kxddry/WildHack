"""FastAPI application entrypoint for the WildHack Retraining Service."""

import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import router, router_v1
from app.api.upload import router as upload_router
from app.api.upload import set_app as _set_upload_app
from app.config import settings
from app.core.registry import ModelRegistry
from app.core.trainer import ModelTrainer
from app.storage import postgres as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create DB pool and HTTP client. Shutdown: close both."""
    logger.info("Starting retraining service...")

    await db.create_engine_pool(settings.database_url)

    http_client = httpx.AsyncClient()

    trainer = ModelTrainer()
    registry = ModelRegistry(
        db=db,
        http_client=http_client,
        prediction_url=settings.prediction_service_url,
    )

    app.state.trainer = trainer
    app.state.registry = registry
    app.state.http_client = http_client
    app.state.startup_time = time.time()

    # Publish the app reference so upload.py can reach trainer/registry/http
    # without creating an import-time cycle against routes.py.
    _set_upload_app(app)

    logger.info("Retraining service ready")
    yield

    logger.info("Shutting down retraining service...")
    await http_client.aclose()
    await db.close_engine()


app = FastAPI(
    title="WildHack Retraining Service",
    description="Periodic model retraining with champion/challenger comparison",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS policy: browsers never talk directly to this service — all dashboard
# traffic goes through the Next.js BFF proxy, which runs same-origin. Leave
# the list empty so any stray cross-origin request is rejected.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=[],
    allow_headers=[],
)

app.include_router(router)
app.include_router(upload_router)
# Dashboard-facing read APIs under /api/v1 (dashboard BFF proxies to these).
app.include_router(router_v1, prefix="/api/v1")

Instrumentator().instrument(app).expose(app)
