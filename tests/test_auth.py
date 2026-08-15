from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_register_returns_created_user(client: AsyncClient, user_payload: dict) -> None:
    r = await client.post("/v1/auth/register", json=user_payload)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == user_payload["email"]
    assert body["role"] == "viewer"
    assert "hashed_password" not in body


async def test_duplicate_email_conflicts(client: AsyncClient, user_payload: dict) -> None:
    await client.post("/v1/auth/register", json=user_payload)
    r = await client.post("/v1/auth/register", json=user_payload)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


async def test_short_password_rejected(client: AsyncClient, user_payload: dict) -> None:
    r = await client.post("/v1/auth/register", json={**user_payload, "password": "short"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


async def test_login_returns_token_pair(client: AsyncClient, user_payload: dict) -> None:
    await client.post("/v1/auth/register", json=user_payload)
    r = await client.post(
        "/v1/auth/login",
        json={"email": user_payload["email"], "password": user_payload["password"]},
    )
    assert r.status_code == 200
    assert {"access_token", "refresh_token"} <= r.json().keys()


async def test_wrong_password_is_indistinguishable_from_unknown_user(
    client: AsyncClient, user_payload: dict
) -> None:
    await client.post("/v1/auth/register", json=user_payload)
    wrong = await client.post(
        "/v1/auth/login", json={"email": user_payload["email"], "password": "wrong-password-x"}
    )
    unknown = await client.post(
        "/v1/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-x"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_me_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/v1/auth/me")).status_code == 401


async def test_refresh_token_cannot_be_used_as_access_token(
    client: AsyncClient, user_payload: dict
) -> None:
    await client.post("/v1/auth/register", json=user_payload)
    tokens = (
        await client.post(
            "/v1/auth/login",
            json={"email": user_payload["email"], "password": user_payload["password"]},
        )
    ).json()

    r = await client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
    )
    assert r.status_code == 401
