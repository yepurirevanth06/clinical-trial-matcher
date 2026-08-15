from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, User


async def _register_and_login(
    client: AsyncClient, db: AsyncSession, email: str, role: Role
) -> str:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "a-sufficiently-long-password"},
    )
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    user.role = role
    await db.flush()

    tokens = (
        await client.post(
            "/v1/auth/login",
            json={"email": email, "password": "a-sufficiently-long-password"},
        )
    ).json()
    return tokens["access_token"]


async def test_viewer_cannot_trigger_sync(client: AsyncClient, db_session: AsyncSession) -> None:
    token = await _register_and_login(client, db_session, "viewer@x.com", Role.VIEWER)
    r = await client.post("/v1/trials/sync", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


async def test_admin_can_trigger_sync(client: AsyncClient, db_session: AsyncSession) -> None:
    token = await _register_and_login(client, db_session, "admin@x.com", Role.ADMIN)
    r = await client.post("/v1/trials/sync", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 202


async def test_anonymous_cannot_list_trials(client: AsyncClient) -> None:
    assert (await client.get("/v1/trials")).status_code == 401
