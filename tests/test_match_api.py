"""POST /v1/patients/{id}/match/{nct_id}.

The evaluator itself is covered in test_eligibility.py against hand-built
trees. What is under test here is the plumbing: loading a tree out of Postgres
without tripping async lazy-loading, serialising a recursive dataclass through
Pydantic, and the three distinct 404s.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.criteria import CriteriaNode, NodeType, Operator
from app.models.patient import Patient, Sex
from app.models.trial import Trial, TrialStatus
from app.models.user import Role
from tests.test_rbac import _register_and_login


async def _auth(client: AsyncClient, db: AsyncSession, email: str) -> dict[str, str]:
    token = await _register_and_login(client, db, email, Role.VIEWER)
    return {"Authorization": f"Bearer {token}"}


async def _patient(db: AsyncSession, **kw) -> Patient:
    defaults = dict(
        external_id="MATCH-1",
        birth_date=date(1990, 1, 1),
        sex=Sex.FEMALE,
        conditions=["E11.9"],
        medications=[],
        lab_values={"egfr": 90.0},
    )
    p = Patient(**{**defaults, **kw})
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _trial(db: AsyncSession, nct_id="NCT00000001") -> Trial:
    t = Trial(
        nct_id=nct_id,
        title="A test trial",
        status=TrialStatus.RECRUITING,
        conditions=[],
        locations=[],
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _tree(db: AsyncSession, trial: Trial) -> CriteriaNode:
    """AND(age >= 18, OR(has E11, takes metformin), NOT-exclusion on C50).

    Deliberately three levels deep: the endpoint's tree loader has to reach the
    grandchildren without lazy-loading, which is where the first version broke.
    """
    root = CriteriaNode(
        trial_id=trial.id, node_type=NodeType.AND, ordering=0, raw_text="Criteria"
    )
    age = CriteriaNode(
        trial_id=trial.id, node_type=NodeType.LEAF, ordering=0,
        field="age", operator=Operator.GTE, value="18", raw_text="Age 18+",
    )
    branch = CriteriaNode(
        trial_id=trial.id, node_type=NodeType.OR, ordering=1, raw_text="Diabetes"
    )
    cond = CriteriaNode(
        trial_id=trial.id, node_type=NodeType.LEAF, ordering=0,
        field="conditions", operator=Operator.CONTAINS, value="E11",
        raw_text="Has type 2 diabetes",
    )
    med = CriteriaNode(
        trial_id=trial.id, node_type=NodeType.LEAF, ordering=1,
        field="medications", operator=Operator.CONTAINS, value="metformin",
        raw_text="On metformin",
    )
    branch.children = [cond, med]
    root.children = [age, branch]
    db.add(root)
    await db.commit()
    return root


@pytest.mark.asyncio
async def test_match_returns_a_nested_explanation(client, db_session):
    headers = await _auth(client, db_session, "m1@x.com")
    trial = await _trial(db_session)
    await _tree(db_session, trial)
    patient = await _patient(db_session)

    r = await client.post(
        f"/v1/patients/{patient.id}/match/{trial.nct_id}", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["verdict"] == "pass"
    assert body["external_id"] == "MATCH-1"
    assert body["nct_id"] == trial.nct_id

    # Three levels: root -> OR -> leaves. If the loader stopped short this is
    # where it shows.
    root = body["explanation"]
    assert len(root["children"]) == 2
    or_node = root["children"][1]
    assert or_node["node_type"] == "or"
    assert len(or_node["children"]) == 2
    assert or_node["children"][0]["raw_text"] == "Has type 2 diabetes"


@pytest.mark.asyncio
async def test_unknown_propagates_to_the_top_level_verdict(client, db_session):
    """A patient missing a lab is not a failure. The top-level verdict has to
    carry that distinction out to the caller, not flatten it."""
    headers = await _auth(client, db_session, "m2@x.com")
    trial = await _trial(db_session, nct_id="NCT00000002")

    root = CriteriaNode(
        trial_id=trial.id, node_type=NodeType.AND, ordering=0, raw_text="Criteria"
    )
    root.children = [
        CriteriaNode(
            trial_id=trial.id, node_type=NodeType.LEAF, ordering=0,
            field="age", operator=Operator.GTE, value="18", raw_text="Age 18+",
        ),
        CriteriaNode(
            trial_id=trial.id, node_type=NodeType.LEAF, ordering=1,
            field="lab_values.egfr", operator=Operator.GTE, value="60",
            raw_text="eGFR >= 60",
        ),
    ]
    db_session.add(root)
    await db_session.commit()

    patient = await _patient(db_session, external_id="MATCH-2", lab_values={})

    r = await client.post(
        f"/v1/patients/{patient.id}/match/{trial.nct_id}", headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "unknown"


@pytest.mark.asyncio
async def test_exclusion_inverts_through_the_api(client, db_session):
    headers = await _auth(client, db_session, "m3@x.com")
    trial = await _trial(db_session, nct_id="NCT00000003")

    root = CriteriaNode(
        trial_id=trial.id, node_type=NodeType.AND, ordering=0, raw_text="Criteria"
    )
    root.children = [
        CriteriaNode(
            trial_id=trial.id, node_type=NodeType.LEAF, ordering=0,
            field="conditions", operator=Operator.CONTAINS, value="C50",
            is_exclusion=True, raw_text="Exclusion: malignancy",
        ),
    ]
    db_session.add(root)
    await db_session.commit()

    # Patient HAS the excluded condition, so the match must fail.
    patient = await _patient(db_session, external_id="MATCH-3", conditions=["C50.919"])

    r = await client.post(
        f"/v1/patients/{patient.id}/match/{trial.nct_id}", headers=headers
    )
    assert r.json()["verdict"] == "fail"


@pytest.mark.asyncio
async def test_unknown_patient_404s(client, db_session):
    import uuid

    headers = await _auth(client, db_session, "m4@x.com")
    trial = await _trial(db_session, nct_id="NCT00000004")
    await _tree(db_session, trial)

    r = await client.post(
        f"/v1/patients/{uuid.uuid4()}/match/{trial.nct_id}", headers=headers
    )
    assert r.status_code == 404
    assert "patient" in r.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_unknown_trial_404s(client, db_session):
    headers = await _auth(client, db_session, "m5@x.com")
    patient = await _patient(db_session, external_id="MATCH-5")

    r = await client.post(
        f"/v1/patients/{patient.id}/match/NCT99999999", headers=headers
    )
    assert r.status_code == 404
    assert "trial" in r.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_trial_without_criteria_404s_distinctly(client, db_session):
    """A trial we have but have not parsed criteria for is a different problem
    for the caller than a trial we do not have. Same status, different message."""
    headers = await _auth(client, db_session, "m6@x.com")
    trial = await _trial(db_session, nct_id="NCT00000006")  # no tree
    patient = await _patient(db_session, external_id="MATCH-6")

    r = await client.post(
        f"/v1/patients/{patient.id}/match/{trial.nct_id}", headers=headers
    )
    assert r.status_code == 404
    assert "criteria" in r.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_match_requires_auth(client, db_session):
    trial = await _trial(db_session, nct_id="NCT00000007")
    patient = await _patient(db_session, external_id="MATCH-7")

    r = await client.post(f"/v1/patients/{patient.id}/match/{trial.nct_id}")
    assert r.status_code == 401
