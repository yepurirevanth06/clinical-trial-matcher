from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core import cache
from app.core.config import settings
from app.db.session import engine
from app.errors import register_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Shutdown cleanup. Nothing to do on startup -- both the Redis client and
    the SQLAlchemy engine are created lazily on first use.

    lifespan= rather than the @app.on_event("startup"/"shutdown") decorators,
    which FastAPI deprecated. Closing here matters because both objects are
    module-level singletons holding open sockets; the process exiting would
    reclaim them, but a clean shutdown should not depend on that.
    """
    yield
    await cache.aclose()
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

register_exception_handlers(app)
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
