import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_token
from app.db.session import get_db
from app.errors import AuthError, PermissionError_
from app.models.user import Role, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], db: DbSession
) -> User:
    payload = decode_token(token, expected_type="access")
    if payload is None:
        raise AuthError("Invalid or expired access token.")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise AuthError("Malformed token subject.") from None

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise AuthError("User not found or inactive.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

# Higher number outranks lower. Keeps RBAC checks to one comparison.
_RANK = {Role.VIEWER: 0, Role.COORDINATOR: 1, Role.ADMIN: 2}


def require_role(
    minimum: Role,
) -> Callable[[User], Coroutine[Any, Any, User]]:
    """Dependency factory. Usage:

        @router.post("/", dependencies=[Depends(require_role(Role.COORDINATOR))])
    """

    async def _check(user: CurrentUser) -> User:
        if _RANK[user.role] < _RANK[minimum]:
            raise PermissionError_(
                f"Requires {minimum.value} role or higher.",
                details={"your_role": user.role.value, "required": minimum.value},
            )
        return user

    return _check
