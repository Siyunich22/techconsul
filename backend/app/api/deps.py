import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import ACCESS_COOKIE, decode_token
from app.models import User, UserRole

DbSession = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class Principal:
    """Текущий пользователь и его организация — источник org_id для всех запросов к БД."""

    user: User

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def org_id(self) -> uuid.UUID:
        return self.user.org_id

    @property
    def role(self) -> UserRole:
        return self.user.role

    @property
    def can_manage(self) -> bool:
        return self.user.role in (UserRole.admin, UserRole.manager)


def get_principal(
    db: DbSession,
    access: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
) -> Principal:
    payload = decode_token(access, "access") if access else None
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    return Principal(user=user)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_manager(principal: CurrentPrincipal) -> Principal:
    if not principal.can_manage:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    return principal


ManagerPrincipal = Annotated[Principal, Depends(require_manager)]
