from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import router
from app.config import settings
from app.core.warehouse import WarehouseRegistry
from app.storage.postgres import create_engine_pool, close_engine, get_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = await create_engine_pool(settings.database_url)

    registry = WarehouseRegistry()
    async with engine.connect() as conn:
        await registry.refresh(conn)
    app.state.warehouse_registry = registry
    app.state.settings = settings

    yield

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
