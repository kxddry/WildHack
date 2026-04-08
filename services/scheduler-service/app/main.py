"""FastAPI application entrypoint for the WildHack Scheduler Service."""

import logging
from contextlib import asynccontextmanager

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import router
from app.config import settings
from app.core.backfill import BackfillRunner
from app.core.pipeline import PipelineOrchestrator
from app.core.quality import QualityChecker
from app.storage import postgres as db_module
from app.storage.postgres import close_engine, create_engine_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create DB pool, HTTP client, orchestrators, start scheduler.
    Shutdown: stop scheduler, close HTTP client and DB pool.
    """
    logger.info("Starting scheduler service...")

    # DB pool
    await create_engine_pool(settings.database_url)

    # HTTP client shared across pipeline runs
    http_client = httpx.AsyncClient()

    # Core components
    orchestrator = PipelineOrchestrator(http_client=http_client)
    quality_checker = QualityChecker(http_client=http_client)
    quality_checker._retrain_url = settings.retraining_service_url
    quality_checker._promote_threshold = settings.shadow_promote_streak_threshold
    backfill_runner = BackfillRunner()

    # Attach to app state so routes can access them
    app.state.db = db_module
    app.state.orchestrator = orchestrator
    app.state.quality_checker = quality_checker
    app.state.backfill_runner = backfill_runner

    async def _run_quality_check_and_persist() -> None:
        result = await quality_checker.run_quality_check(from_db=db_module)
        if result.get("metrics"):
            try:
                await db_module.save_quality_check(result["metrics"])
            except Exception:
                logger.exception("Failed to persist quality check metrics")

    # APScheduler — three interval jobs
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        orchestrator.run_prediction_cycle,
        "interval",
        minutes=settings.prediction_interval_minutes,
        kwargs={"from_db": db_module},
        id="prediction_cycle",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_quality_check_and_persist,
        "interval",
        minutes=settings.quality_check_interval_minutes,
        id="quality_check",
        replace_existing=True,
    )
    scheduler.add_job(
        backfill_runner.run_backfill,
        "interval",
        minutes=30,
        kwargs={"from_db": db_module},
        id="backfill_target_2h",
        replace_existing=True,
    )
    scheduler.start()
    app.state.scheduler = scheduler

    logger.info(
        "Scheduler service ready — prediction every %d min, quality check every %d min, backfill every 30 min",
        settings.prediction_interval_minutes,
        settings.quality_check_interval_minutes,
    )
    yield

    # Shutdown
    logger.info("Shutting down scheduler service...")
    scheduler.shutdown(wait=False)
    await http_client.aclose()
    await close_engine()


app = FastAPI(
    title="WildHack Scheduler Service",
    description="Orchestrates the ML production pipeline on a schedule",
    version="1.0.0",
    lifespan=lifespan,
)

# Internal service — the dashboard BFF never calls the scheduler directly.
# Leave the origin list empty; the protected control routes remain reachable
# from in-cluster callers that supply X-Internal-Token.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=[],
    allow_headers=[],
)

app.include_router(router)

Instrumentator().instrument(app).expose(app)
