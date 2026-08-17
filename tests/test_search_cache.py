"""Redis cache on the search path.

These tests exist because the rest of the suite passes with the cache removed
entirely -- correct results prove nothing about whether the cache is consulted.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import bump_version
from app.models.trial import Trial, TrialStatus
from app.models.user import Role
from tests.test_rbac import _register_and_login

URL = "/v1/trials/search"


async def _auth(client: AsyncClient, db: AsyncSession, email: str) -> dict[str, str]:
    token = await _register_and_login(client, db, email, Role.VIEWER)
    return {"Authorization": f"Bearer {token}"}


async def _mk(db: AsyncSession, *, tag: str, title: str) -> Trial:
    t = Trial(
        nct_id=f"NCT{tag}",
        title=title,
        status=TrialStatus.RECRUITING,
        conditions=[],
        locations=[],
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _search(client, headers, q):
    r = await client.get(URL, params={"q": q}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_hit_and_miss_are_byte_identical(client, db_session):
    """The cached value is a serialised OffsetPage, so it round-trips through
    JSON: a date column leaves as a string and has to be re-parsed on the way
    back in. If model_validate cannot rebuild it, this is where it shows.

    Distinct search terms per test throughout this file -- the autouse fixture
    closes the Redis client between tests but does not FLUSHDB, so keys survive.
    """
    headers = await _auth(client, db_session, "cache1@x.com")
    await _mk(db_session, tag="9001", title="Warfarin dosing cachetesta")

    miss = await _search(client, headers, "cachetesta")
    hit = await _search(client, headers, "cachetesta")

    assert miss == hit
    assert len(hit["items"]) == 1


@pytest.mark.asyncio
async def test_second_request_does_not_reach_postgres(client, db_session):
    """The load-bearing test. Delete the row out from under the cache and it
    should still come back -- which is only possible if the second request
    never queried the database. Without this, a no-op cache passes everything.
    """
    headers = await _auth(client, db_session, "cache2@x.com")
    trial = await _mk(db_session, tag="9002", title="Metoprolol cachetestb")

    first = await _search(client, headers, "cachetestb")
    assert len(first["items"]) == 1

    await db_session.execute(delete(Trial).where(Trial.id == trial.id))
    await db_session.commit()

    second = await _search(client, headers, "cachetestb")
    assert second == first  # served from Redis, not Postgres


@pytest.mark.asyncio
async def test_bump_version_invalidates(client, db_session):
    """Version-key invalidation: the counter is baked into the cache key, so
    INCR makes every existing entry unreachable without a KEYS scan.
    """
    headers = await _auth(client, db_session, "cache3@x.com")
    await _mk(db_session, tag="9003", title="Lisinopril cachetestc")

    first = await _search(client, headers, "cachetestc")
    assert len(first["items"]) == 1

    # New matching row would be invisible behind a stale cache.
    await _mk(db_session, tag="9004", title="Enalapril cachetestc")
    stale = await _search(client, headers, "cachetestc")
    assert len(stale["items"]) == 1, "expected the stale cached page"

    await bump_version()

    fresh = await _search(client, headers, "cachetestc")
    assert len(fresh["items"]) == 2


@pytest.mark.asyncio
async def test_distinct_params_do_not_share_a_key(client, db_session):
    """make_key hashes all the query params, not just q -- a different limit
    must not collide with a cached page for the same search term.
    """
    headers = await _auth(client, db_session, "cache4@x.com")
    for n in range(3):
        await _mk(db_session, tag=f"901{n}", title="Amlodipine cachetestd")

    r1 = await client.get(URL, params={"q": "cachetestd", "limit": 1}, headers=headers)
    r2 = await client.get(URL, params={"q": "cachetestd", "limit": 3}, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(r1.json()["items"]) == 1
    assert len(r2.json()["items"]) == 3
