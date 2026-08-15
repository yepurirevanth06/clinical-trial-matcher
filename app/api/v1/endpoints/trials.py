"""Trial endpoints.

Week 1 ships the read path and the RBAC wiring. Week 2 replaces the stub
listing with the ClinicalTrials.gov sync + full-text search.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession, require_role
from app.errors import NotFoundError
from app.models.trial import Trial, TrialStatus
from app.models.user import Role

router = APIRouter(prefix="/trials", tags=["trials"])


@router.get("")
async def list_trials(
    db: DbSession,
    _: CurrentUser,
    status_filter: TrialStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    stmt = select(Trial).order_by(Trial.created_at.desc()).limit(limit + 1)
    if status_filter is not None:
        stmt = stmt.where(Trial.status == status_filter)

    rows = list(await db.scalars(stmt))
    has_more = len(rows) > limit
    items = rows[:limit]

    # TODO(week 2): swap to keyset pagination on (created_at, id).
    return {
        "items": [
            {
                "id": str(t.id),
                "nct_id": t.nct_id,
                "title": t.title,
                "status": t.status.value,
                "phase": t.phase,
                "conditions": t.conditions,
            }
            for t in items
        ],
        "has_more": has_more,
        "next_cursor": None,
    }


@router.get("/{trial_id}")
async def get_trial(trial_id: uuid.UUID, db: DbSession, _: CurrentUser) -> dict:
    trial = await db.scalar(select(Trial).where(Trial.id == trial_id))
    if trial is None:
        raise NotFoundError("Trial not found.", details={"trial_id": str(trial_id)})
    return {
        "id": str(trial.id),
        "nct_id": trial.nct_id,
        "title": trial.title,
        "brief_summary": trial.brief_summary,
        "status": trial.status.value,
        "phase": trial.phase,
        "conditions": trial.conditions,
        "locations": trial.locations,
    }


@router.post(
    "/sync",
    dependencies=[Depends(require_role(Role.ADMIN))],
    status_code=202,
)
async def trigger_sync() -> dict:
    # Week 2: enqueue the Celery sync task instead of returning a stub.
    return {"status": "not_implemented", "detail": "ClinicalTrials.gov sync lands in week 2."}
