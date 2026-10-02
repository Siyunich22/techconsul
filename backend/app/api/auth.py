from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from app.api.deps import CurrentPrincipal, DbSession
from app.core.mailer import Mailer, get_mailer
from app.core.security import REFRESH_COOKIE, hash_password, verify_password
from app.schemas.auth import (
    AcceptInviteIn,
    ChangePasswordIn,
    ForgotIn,
    InvitationInfo,
    LoginIn,
    MeOut,
    MePatch,
    RegisterIn,
    ResetIn,
)
from app.services import auth as svc
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["me"])

RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]


def _ua(request: Request) -> str:
    return request.headers.get("user-agent", "")


@router.post("/register", response_model=MeOut, status_code=status.HTTP_201_CREATED)
def register(data: RegisterIn, db: DbSession, request: Request, response: Response):
    user = svc.register(db, data)
    svc.issue_session(db, user, response, _ua(request))
    db.commit()
    return user


@router.post("/login", response_model=MeOut)
def login(data: LoginIn, db: DbSession, request: Request, response: Response):
    user = svc.authenticate(db, data.email, data.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    user.last_login_at = datetime.now(UTC)
    svc.issue_session(db, user, response, _ua(request))
    audit(db, "auth.login", org_id=user.org_id, user_id=user.id)
    db.commit()
    return user


@router.post("/refresh", response_model=MeOut)
def refresh(db: DbSession, request: Request, response: Response, token: RefreshCookie = None):
    user = svc.rotate_session(db, token, response, _ua(request))
    db.commit()
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(db: DbSession, response: Response, token: RefreshCookie = None):
    svc.logout(db, token, response)
    db.commit()


@router.post("/forgot", status_code=status.HTTP_204_NO_CONTENT)
def forgot(data: ForgotIn, db: DbSession, mailer: MailerDep):
    svc.request_password_reset(db, mailer, data.email)


@router.post("/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset(data: ResetIn, db: DbSession):
    svc.reset_password(db, data.token, data.password)
    db.commit()


@router.get("/invitations/{token}", response_model=InvitationInfo)
def get_invitation(token: str, db: DbSession):
    user = svc.invitation_info(db, token)
    return InvitationInfo(
        email=user.email,
        full_name=user.full_name,
        organization_name=user.organization.name,
        role=user.role,
    )


@router.post("/invitations/accept", response_model=MeOut)
def accept_invitation(data: AcceptInviteIn, db: DbSession, request: Request, response: Response):
    user = svc.accept_invitation(db, data.token, data.password, data.full_name)
    svc.issue_session(db, user, response, _ua(request))
    db.commit()
    return user


@me_router.get("", response_model=MeOut)
def get_me(principal: CurrentPrincipal):
    return principal.user


@me_router.patch("", response_model=MeOut)
def patch_me(data: MePatch, principal: CurrentPrincipal, db: DbSession):
    user = principal.user
    for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(user, field, value)
    audit(db, "user.profile_updated", org_id=user.org_id, user_id=user.id)
    db.commit()
    return user


@me_router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(data: ChangePasswordIn, principal: CurrentPrincipal, db: DbSession):
    user = principal.user
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Текущий пароль указан неверно")
    user.password_hash = hash_password(data.new_password)
    audit(db, "user.password_changed", org_id=user.org_id, user_id=user.id)
    db.commit()
