"""Patient endpoints."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession, require_role
from app.errors import ConflictError, NotFoundError
from app.models.patient import Patient
from app.models.trial import Trial
from app.models.user import Role
from app.schemas.match import MatchResult, NodeExplanation
from app.schemas.patient import PatientCreate, PatientOut
from app.services.eligibility import evaluate, load_tree

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


@router.post("/{patient_id}/match/{nct_id}", response_model=MatchResult)
async def match_patient_to_trial(
    patient_id: uuid.UUID,
    nct_id: str,
    db: DbSession,
    _: CurrentUser,
) -> MatchResult:
    """Evaluate one patient against one trial's eligibility criteria.

    POST rather than GET despite being read-only: evaluation is a computation
    over two resources rather than the retrieval of a stored one, and the
    result is not addressable or cacheable as a resource of its own.

    404s separately for the patient, the trial, and a trial with no criteria --
    "we have no criteria for this trial yet" is a different problem for the
    caller than "no such trial", and collapsing them would hide which.
    """
    patient = await db.get(Patient, patient_id)
    if patient is None:
        raise NotFoundError(f"No patient with id {patient_id}.")

    trial = await db.scalar(select(Trial).where(Trial.nct_id == nct_id))
    if trial is None:
        raise NotFoundError(f"No trial with nct_id {nct_id}.")

    root = await load_tree(db, trial.id)
    if root is None:
        raise NotFoundError(f"Trial {nct_id} has no parsed eligibility criteria.")

    result = evaluate(root, patient)

    return MatchResult(
        patient_id=patient.id,
        external_id=patient.external_id,
        nct_id=trial.nct_id,
        trial_title=trial.title,
        verdict=result.verdict,
        explanation=NodeExplanation.model_validate(result),
    )
