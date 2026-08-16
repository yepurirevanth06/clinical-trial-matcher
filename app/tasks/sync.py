"""Trial sync task."""

import asyncio
import logging
from datetime import date

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.cache import bump_version_sync
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.trial import Trial
from app.services.ctgov import ClinicalTrialsClient

logger = logging.getLogger(__name__)


async def _sync(condition: str | None, max_pages: int) -> dict:
    # The engine in app.db.session is a module-level singleton whose asyncpg
    # pool binds to the first event loop that touches it. uvicorn runs one
    # long-lived loop, so the API never notices. Celery's sync task wrapper
    # calls asyncio.run() per task -- a new loop each time -- so by task 2 the
    # pooled connections belong to a closed loop ("attached to a different
    # loop"). A task-local engine keeps pool lifetime == loop lifetime.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    client = ClinicalTrialsClient()
    seen = 0
    batch: list[dict] = []

    try:
        async with session_factory() as session:
            async for study in client.iter_studies(condition=condition, max_pages=max_pages):
                study.pop("eligibility_text", None)  # week 3 parses this into the tree
                study["last_synced_at"] = date.today()
                batch.append(study)
                seen += 1

                if len(batch) >= 200:
                    await _upsert(session, batch)
                    batch.clear()

            if batch:
                await _upsert(session, batch)
            await session.commit()
    finally:
        # Must dispose inside the loop that created the pool.
        await engine.dispose()

    logger.info("ctgov sync complete: %s studies", seen)
    return {"studies_seen": seen}


async def _upsert(session, rows: list[dict]) -> None:
    """INSERT ... ON CONFLICT DO UPDATE, keyed on nct_id.

    This is what makes the sync idempotent: re-running it over the same window
    updates rows in place instead of raising a unique-violation or creating
    duplicates. A pipeline you cannot safely re-run is a pipeline you cannot
    safely backfill.
    """
    stmt = insert(Trial).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Trial.nct_id],
        set_={
            "title": stmt.excluded.title,
            "brief_summary": stmt.excluded.brief_summary,
            "status": stmt.excluded.status,
            "start_date": stmt.excluded.start_date,
            "phase": stmt.excluded.phase,
            "conditions": stmt.excluded.conditions,
            "locations": stmt.excluded.locations,
            "last_synced_at": stmt.excluded.last_synced_at,
        },
    )
    await session.execute(stmt)


@celery_app.task(name="sync_trials", bind=True, max_retries=3)
def sync_trials(self, condition: str | None = None, max_pages: int = 10) -> dict:
    try:
        result = asyncio.run(_sync(condition, max_pages))
        # After the sync, not inside _sync: if the crawl raises partway, the
        # cache should still serve the old results rather than being
        # invalidated for work that never landed.
        result["cache_version"] = bump_version_sync()
        return result
    except Exception as exc:
        logger.exception("sync_trials failed")
        raise self.retry(exc=exc, countdown=60) from exc
