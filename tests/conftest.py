import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.deps import get_current_user  # noqa: F401  (kept for override examples)
from app.db.registry import Base
from app.db.session import get_db
from app.main import app

# Tests run on the SAME engine as production. Keyset pagination depends on
# Postgres-specific semantics -- bytewise uuid ordering, now() as the
# TRANSACTION timestamp, and the composite btree -- none of which SQLite
# reproduces. Week 2 adds to_tsvector, which SQLite cannot run at all.
TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    f"postgresql+asyncpg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/ctm_test",
)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def user_payload() -> dict:
    return {
        "email": "coordinator@example.com",
        "password": "a-sufficiently-long-password",
        "full_name": "Test Coordinator",
    }


@pytest.fixture(autouse=True)
async def _reset_cache_client():
    """Each test gets its own event loop, so the cached Redis client from the
    previous test is bound to a dead one. Reset before and after."""
    from app.core import cache

    await cache.aclose()
    yield
    await cache.aclose()
