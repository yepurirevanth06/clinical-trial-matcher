"""Keyset pagination correctness under concurrent writes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor
from app.models.trial import Trial, TrialStatus
from app.models.user import Role
from tests.test_rbac import _register_and_login

BASE = datetime(2026, 1, 1, tzinfo=UTC)
URL = "/v1/trials"


async def _auth(client: AsyncClient, db: AsyncSession, email: str) -> dict[str, str]:
    token = await _register_and_login(client, db, email, Role.VIEWER)
    return {"Authorization": f"Bearer {token}"}


async def _mk(db: AsyncSession, *, secs: int, tag: str) -> Trial:
    """Insert one trial at an explicit created_at.

    created_at is passed explicitly rather than left to server_default. now() is
    the TRANSACTION timestamp in Postgres, so every row a test creates would
    otherwise share one value -- fine for the tiebreaker test below, useless when
    a test needs rows in a known order.
    """
    t = Trial(
        nct_id=f"NCT{tag}",
        title=f"Trial {tag}",
        status=TrialStatus.RECRUITING,
        conditions=[],
        locations=[],
        created_at=BASE + timedelta(seconds=secs),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _page(client, headers, *, limit, cursor=None):
    params = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    r = await client.get(URL, params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _walk_all(client, headers, *, limit, first=None):
    """Collect every id across a full pagination walk."""
    page = first or await _page(client, headers, limit=limit)
    ids = [t["id"] for t in page["items"]]
    cursor = page["next_cursor"]
    for _ in range(20):  # bounded: a broken cursor should fail, not hang
        if not cursor:
            break
        page = await _page(client, headers, limit=limit, cursor=cursor)
        ids.extend(t["id"] for t in page["items"])
        cursor = page["next_cursor"]
    return ids


@pytest.mark.asyncio
async def test_insert_between_pages_no_skip_no_duplicate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, db_session, "p1@x.com")
    originals = {str((await _mk(db_session, secs=i, tag=f"{i:05d}")).id) for i in range(9)}

    page1 = await _page(client, headers, limit=4)
    assert page1["has_more"] is True

    # A concurrent writer -- read: the Celery sync task -- lands a new row while
    # the client sits between pages. Under OFFSET this shifts every row down one
    # slot and page 2 re-serves the last row of page 1.
    await _mk(db_session, secs=9_999, tag="99999")

    seen = await _walk_all(client, headers, limit=4, first=page1)

    assert len(seen) == len(set(seen)), "a row was served on two pages"
    # The new row sorts ahead of page 1, so its absence is correct, not a skip:
    # the cursor pins the client to a stable view of the tail of the list.
    assert set(seen) == originals


@pytest.mark.asyncio
async def test_backdated_insert_is_not_skipped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The other direction: a row landing BEHIND the cursor must still appear.

    A flipped comparison operator passes the test above and fails this one.
    """
    headers = await _auth(client, db_session, "p2@x.com")
    for i in range(6):
        await _mk(db_session, secs=i * 10, tag=f"1{i:04d}")

    page1 = await _page(client, headers, limit=3)  # DESC -> 50, 40, 30
    page1_ids = {t["id"] for t in page1["items"]}

    late = await _mk(db_session, secs=25, tag="18888")  # falls after the cursor
    page2 = await _page(client, headers, limit=3, cursor=page1["next_cursor"])
    page2_ids = [t["id"] for t in page2["items"]]

    assert str(late.id) in page2_ids, "row inserted behind the cursor was skipped"
    assert not page1_ids & set(page2_ids)


@pytest.mark.asyncio
async def test_identical_timestamps_tiebroken_by_uuid(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Five rows sharing one created_at, exactly as a sync batch produces.

    This is not a contrived edge case: created_at defaults to now(), which in
    Postgres is the TRANSACTION timestamp, so all 100 studies from one
    sync_trials run carry a byte-identical value. Without the uuid leg of the
    tuple this walk drops rows or loops forever.
    """
    headers = await _auth(client, db_session, "p3@x.com")
    ids = {str((await _mk(db_session, secs=0, tag=f"2{i:04d}")).id) for i in range(5)}

    seen = await _walk_all(client, headers, limit=2)

    assert len(seen) == 5, f"expected 5 rows, walked {len(seen)}"
    assert set(seen) == ids


@pytest.mark.asyncio
async def test_malformed_cursor_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, db_session, "p4@x.com")
    r = await client.get(URL, params={"cursor": "not-a-cursor"}, headers=headers)
    assert r.status_code == 400


def test_cursor_roundtrip_preserves_microseconds() -> None:
    # TIMESTAMPTZ keeps microseconds. If isoformat() truncated them the cursor
    # would land a hair off the real row and silently drop or repeat one row per
    # page -- the worst kind of pagination bug, since nothing errors.
    c = Cursor(
        created_at=datetime(2026, 3, 4, 5, 6, 7, 891_234, tzinfo=UTC),
        id=uuid.uuid4(),
    )
    assert Cursor.decode(c.encode()) == c
