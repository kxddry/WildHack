import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import router
from app.config import settings
from app.storage.postgres import create_engine_pool, close_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting dispatcher service...")
    await create_engine_pool(settings.database_url)
    logger.info("Dispatcher service ready")

    yield

    logger.info("Shutting down dispatcher service...")
    await close_engine()


app = FastAPI(
    title="WildHack Dispatcher Service",
    description="Automated transport dispatching based on demand forecasts",
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

Instrumentator().instrument(app).expose(app)
