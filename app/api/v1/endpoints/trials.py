"""Trial endpoints.

Week 1 ships the read path and the RBAC wiring. Week 2 replaces the stub
listing with the ClinicalTrials.gov sync + full-text search.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession, require_role
from app.core.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Cursor,
    InvalidCursorError,
    apply_keyset,
)
from app.errors import BadRequestError, NotFoundError
from app.schemas.pagination import Page
from app.schemas.trial import TrialRead
from app.models.trial import Trial, TrialStatus
from app.models.user import Role

router = APIRouter(prefix="/trials", tags=["trials"])


@router.get("", response_model=Page[TrialRead])
async def list_trials(
    db: DbSession,
    _: CurrentUser,
    status_filter: TrialStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = Query(default=None, description="Opaque token from next_cursor"),
) -> Page[TrialRead]:
    try:
        parsed = Cursor.decode(cursor) if cursor else None
    except InvalidCursorError as exc:
        # 400, not 422: the cursor is structurally a valid string, so FastAPI's
        # validation passes. It is the decoded contents that are bad.
        raise BadRequestError("invalid pagination cursor") from exc

    stmt = select(Trial)
    if status_filter is not None:
        stmt = stmt.where(Trial.status == status_filter)

    # Filters must stay constant across a pagination run. The cursor encodes a
    # position, not a query -- changing `status` mid-walk yields a coherent but
    # meaningless traversal. Stricter APIs hash the filter set into the cursor
    # and reject a mismatch; we document the contract instead.
    stmt = apply_keyset(
        stmt,
        sort_col=Trial.created_at,
        tiebreak_col=Trial.id,
        cursor=parsed,
        limit=limit,
    )

    rows = list(await db.scalars(stmt))
    has_more = len(rows) > limit
    rows = rows[:limit]  # drop the sentinel

    # Built AFTER the slice. Build it before and the cursor points at the first
    # row of the NEXT page rather than the last row of this one, so every page
    # silently loses its first row -- no error, just missing data.
    next_cursor = (
        Cursor(created_at=rows[-1].created_at, id=rows[-1].id).encode()
        if has_more and rows
        else None
    )

    return Page[TrialRead](
        items=[TrialRead.model_validate(r) for r in rows],
        next_cursor=next_cursor,
        has_more=has_more,
    )

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
