"""Tests for the patient endpoints."""

import uuid
from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role
from tests.test_rbac import _register_and_login


def _payload(external_id: str = "SYN-0001") -> dict:
    return {
        "external_id": external_id,
        "birth_date": "1990-06-15",
        "sex": "female",
        "conditions": ["E11.9"],
        "medications": ["metformin"],
        "lab_values": {"egfr": 72.0},
    }


def _expected_age(birth: date, today: date | None = None) -> int:
    today = today or date.today()
    had_birthday = (today.month, today.day) >= (birth.month, birth.day)
    return today.year - birth.year - (0 if had_birthday else 1)


async def test_coordinator_can_create_patient(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, db_session, "coord@x.com", Role.COORDINATOR)
    r = await client.post(
        "/v1/patients", json=_payload(), headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["external_id"] == "SYN-0001"
    assert body["conditions"] == ["E11.9"]
    assert body["age"] == _expected_age(date(1990, 6, 15))
    uuid.UUID(body["id"])  # raises if it isn't a valid UUID


async def test_viewer_cannot_create_patient(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, db_session, "viewer2@x.com", Role.VIEWER)
    r = await client.post(
        "/v1/patients", json=_payload(), headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


async def test_anonymous_cannot_create_patient(client: AsyncClient) -> None:
    r = await client.post("/v1/patients", json=_payload())
    assert r.status_code == 401


async def test_duplicate_external_id_conflicts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, db_session, "coord2@x.com", Role.COORDINATOR)
    headers = {"Authorization": f"Bearer {token}"}
    first = await client.post("/v1/patients", json=_payload("SYN-DUP"), headers=headers)
    assert first.status_code == 201

    second = await client.post("/v1/patients", json=_payload("SYN-DUP"), headers=headers)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


async def test_future_birth_date_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, db_session, "coord3@x.com", Role.COORDINATOR)
    payload = _payload("SYN-FUTURE")
    payload["birth_date"] = (date.today() + timedelta(days=1)).isoformat()

    r = await client.post(
        "/v1/patients", json=payload, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


async def test_get_unknown_patient_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    token = await _register_and_login(client, db_session, "viewer3@x.com", Role.VIEWER)
    r = await client.get(
        f"/v1/patients/{uuid.uuid4()}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
