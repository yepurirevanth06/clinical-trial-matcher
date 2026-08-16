"""Ranked full-text search over the generated tsvector column."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trial import Trial, TrialStatus
from app.models.user import Role
from tests.test_rbac import _register_and_login

URL = "/v1/trials/search"


async def _auth(client: AsyncClient, db: AsyncSession, email: str) -> dict[str, str]:
    token = await _register_and_login(client, db, email, Role.VIEWER)
    return {"Authorization": f"Bearer {token}"}


async def _mk(
    db: AsyncSession,
    *,
    tag: str,
    title: str,
    summary: str | None = None,
    conditions: list[str] | None = None,
) -> Trial:
    """Insert a trial. search_vector is GENERATED, so it is never passed here --
    Postgres computes it on INSERT and refresh() reads it back."""
    t = Trial(
        nct_id=f"NCT{tag}",
        title=title,
        brief_summary=summary,
        status=TrialStatus.RECRUITING,
        conditions=conditions or [],
        locations=[],
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _search(client, headers, q, **params):
    r = await client.get(URL, params={"q": q, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_title_outranks_summary(client, db_session):
    """The A/B/C setweight in the generated column is what makes this true.
    Drop the weights and both rows tie, so this is the test that catches it."""
    headers = await _auth(client, db_session, "rank@x.com")
    await _mk(db_session, tag="0001", title="Metformin dosing study")
    await _mk(db_session, tag="0002", title="Unrelated study", summary="Uses metformin as a comparator.")

    body = await _search(client, headers, "metformin")
    assert len(body["items"]) == 2
    assert body["items"][0]["nct_id"] == "NCT0001"


@pytest.mark.asyncio
async def test_stemming_matches_inflections(client, db_session):
    """to_tsvector('english', ...) stems, so this is not substring matching:
    'insulins' and 'insulin' reduce to the same lexeme."""
    headers = await _auth(client, db_session, "stem@x.com")
    await _mk(db_session, tag="0003", title="Insulin sensitivity trial")

    body = await _search(client, headers, "insulins")
    assert [i["nct_id"] for i in body["items"]] == ["NCT0003"]


@pytest.mark.asyncio
async def test_conditions_are_searchable(client, db_session):
    """conditions::text renders the jsonb array with brackets and quotes; the
    parser discards those as non-words. Ugly but immutable, which a generated
    column requires. If someone 'cleans up' that cast, this fails."""
    headers = await _auth(client, db_session, "cond@x.com")
    await _mk(db_session, tag="0004", title="Study A", conditions=["Hypertension"])

    body = await _search(client, headers, "hypertension")
    assert [i["nct_id"] for i in body["items"]] == ["NCT0004"]


@pytest.mark.asyncio
async def test_offset_paging_does_not_repeat_rows(client, db_session):
    """has_more comes from the limit+1 sentinel, not a COUNT. The id tiebreak
    is what stops a rank tie from showing the same row on both pages."""
    headers = await _auth(client, db_session, "page@x.com")
    for n in range(3):
        await _mk(db_session, tag=f"001{n}", title="Asthma inhaler trial")

    first = await _search(client, headers, "asthma", limit=2, offset=0)
    assert len(first["items"]) == 2
    assert first["has_more"] is True

    second = await _search(client, headers, "asthma", limit=2, offset=2)
    assert second["has_more"] is False
    assert second["offset"] == 2

    ids = {i["nct_id"] for i in first["items"]} | {i["nct_id"] for i in second["items"]}
    assert len(ids) == 3


@pytest.mark.asyncio
async def test_no_match_returns_empty_page(client, db_session):
    headers = await _auth(client, db_session, "none@x.com")
    await _mk(db_session, tag="0020", title="Cardiology study")

    body = await _search(client, headers, "zzzznotaword")
    assert body["items"] == []
    assert body["has_more"] is False


@pytest.mark.asyncio
async def test_short_query_is_rejected(client, db_session):
    """min_length=2 on the Query, so FastAPI validates before the handler."""
    headers = await _auth(client, db_session, "short@x.com")
    r = await client.get(URL, params={"q": "a"}, headers=headers)
    assert r.status_code == 422
