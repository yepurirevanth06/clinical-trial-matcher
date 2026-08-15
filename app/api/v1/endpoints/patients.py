"""Patient endpoints."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession, require_role
from app.errors import ConflictError, NotFoundError
from app.models.patient import Patient
from app.models.user import Role
from app.schemas.patient import PatientCreate, PatientOut

router = APIRouter(prefix="/patients", tags=["patients"])


@router.post(
    "",
    response_model=PatientOut,
    status_code=status.HTTP_201_CREATED,
    # Coordinators and admins only. Anonymous callers fail earlier, at the
    # bearer-token step, and get 401 rather than 403 — different failures,
    # different codes.
    dependencies=[Depends(require_role(Role.COORDINATOR))],
)
async def create_patient(payload: PatientCreate, db: DbSession) -> Patient:
    existing = await db.scalar(
        select(Patient).where(Patient.external_id == payload.external_id)
    )
    if existing is not None:
        raise ConflictError(
            "A patient with that external_id already exists.",
            details={"external_id": payload.external_id},
        )

    patient = Patient(**payload.model_dump())
    db.add(patient)

    # flush(), not commit(). flush sends the INSERT so the database assigns
    # defaults (created_at) and we can read them back, but leaves the
    # transaction open. get_db in app/db/session.py commits once the request
    # succeeds and rolls back if anything after this raises — so a later
    # failure can't leave a half-written patient behind.
    await db.flush()
    await db.refresh(patient)
    return patient


@router.get("/{patient_id}", response_model=PatientOut)
async def get_patient(patient_id: uuid.UUID, db: DbSession, _: CurrentUser) -> Patient:
    # patient_id is typed as uuid.UUID, so FastAPI rejects a malformed id with
    # 422 before this function runs. Only a well-formed but unknown id gets here.
    patient = await db.scalar(select(Patient).where(Patient.id == patient_id))
    if patient is None:
        raise NotFoundError("Patient not found.", details={"patient_id": str(patient_id)})
    return patient
