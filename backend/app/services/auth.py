import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.mailer import Mailer
from app.core.security import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    new_opaque_token,
    verify_password,
)
from app.models import Organization, RefreshSession, TokenPurpose, User, UserRole, UserToken
from app.schemas.auth import RegisterIn
from app.services.audit import audit

REFRESH_COOKIE_PATH = "/api/v1/auth"


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == normalize_email(email)))


def register(db: Session, data: RegisterIn) -> User:
    if get_user_by_email(db, data.user.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Пользователь с таким email уже зарегистрирован")
    if db.scalar(select(Organization).where(Organization.bin == data.organization.bin)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Организация с таким БИН уже зарегистрирована")

    org = Organization(
        name=data.organization.name, bin=data.organization.bin, address=data.organization.address
    )
    db.add(org)
    db.flush()
    user = User(
        org_id=org.id,
        email=normalize_email(data.user.email),
        password_hash=hash_password(data.user.password),
        full_name=data.user.full_name,
        role=UserRole.manager,
        position=data.user.position,
        phone=data.user.phone,
    )
    db.add(user)
    db.flush()
    audit(db, "org.register", org_id=org.id, user_id=user.id, org_name=org.name, bin=org.bin)
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return None
    return user


def issue_session(db: Session, user: User, response: Response, user_agent: str = "") -> None:
    s = get_settings()
    expires_at = _now() + timedelta(days=s.refresh_token_ttl_days)
    session = RefreshSession(user_id=user.id, expires_at=expires_at, user_agent=user_agent[:500])
    db.add(session)
    db.flush()
    _set_cookies(
        response,
        access=create_access_token(user.id, user.org_id, user.role.value),
        refresh=create_refresh_token(user.id, session.id, expires_at),
    )


def rotate_session(db: Session, refresh_token: str | None, response: Response, user_agent: str = "") -> User:
    payload = decode_token(refresh_token, "refresh") if refresh_token else None
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")
    session = db.get(RefreshSession, uuid.UUID(payload["jti"]))
    if session is None or session.expires_at <= _now():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")
    if session.revoked_at is not None:
        # Повторное использование отозванного токена — признак кражи: отзываем все сессии пользователя.
        revoke_all_sessions(db, session.user_id)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")
    session.revoked_at = _now()
    issue_session(db, user, response, user_agent)
    return user


def logout(db: Session, refresh_token: str | None, response: Response) -> None:
    payload = decode_token(refresh_token, "refresh") if refresh_token else None
    if payload:
        session = db.get(RefreshSession, uuid.UUID(payload["jti"]))
        if session and session.revoked_at is None:
            session.revoked_at = _now()
    clear_cookies(response)


def revoke_all_sessions(db: Session, user_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


def _set_cookies(response: Response, *, access: str, refresh: str) -> None:
    s = get_settings()
    common = {"httponly": True, "secure": s.cookie_secure, "samesite": "lax"}
    response.set_cookie(ACCESS_COOKIE, access, max_age=s.access_token_ttl_min * 60, path="/", **common)
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=s.refresh_token_ttl_days * 86400,
        path=REFRESH_COOKIE_PATH,
        **common,
    )


def clear_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


# --- одноразовые токены: сброс пароля и приглашения ---


def _create_user_token(db: Session, user: User, purpose: TokenPurpose, ttl: timedelta) -> str:
    token, token_hash = new_opaque_token()
    db.add(UserToken(user_id=user.id, purpose=purpose, token_hash=token_hash, expires_at=_now() + ttl))
    return token


def _consume_user_token(db: Session, token: str, purpose: TokenPurpose, *, mark_used: bool) -> User:
    ut = db.scalar(
        select(UserToken).where(UserToken.token_hash == hash_token(token), UserToken.purpose == purpose)
    )
    if ut is None or ut.used_at is not None or ut.expires_at <= _now():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ссылка недействительна или устарела")
    user = db.get(User, ut.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ссылка недействительна или устарела")
    if mark_used:
        ut.used_at = _now()
    return user


def request_password_reset(db: Session, mailer: Mailer, email: str) -> None:
    """Всегда «успешно» для внешнего наблюдателя — не раскрываем, зарегистрирован ли email."""
    user = get_user_by_email(db, email)
    if user is None or not user.is_active or user.password_hash is None:
        return
    s = get_settings()
    token = _create_user_token(db, user, TokenPurpose.reset, timedelta(hours=s.reset_token_ttl_hours))
    audit(db, "auth.reset_requested", org_id=user.org_id, user_id=user.id)
    db.commit()
    mailer.send(
        user.email,
        "ТехОценка: восстановление пароля",
        f"Здравствуйте, {user.full_name}!\n\n"
        f"Для смены пароля перейдите по ссылке (действует {s.reset_token_ttl_hours} ч):\n"
        f"{s.public_url}/reset/{token}\n\n"
        "Если вы не запрашивали смену пароля, проигнорируйте это письмо.",
    )


def reset_password(db: Session, token: str, password: str) -> User:
    user = _consume_user_token(db, token, TokenPurpose.reset, mark_used=True)
    user.password_hash = hash_password(password)
    revoke_all_sessions(db, user.id)
    audit(db, "auth.password_reset", org_id=user.org_id, user_id=user.id)
    return user


def invite_user(
    db: Session,
    mailer: Mailer,
    *,
    org: Organization,
    inviter: User,
    email: str,
    full_name: str,
    role: UserRole,
) -> tuple[User, str]:
    if role == UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Администратора платформы нельзя пригласить")
    existing = get_user_by_email(db, email)
    if existing and (existing.org_id != org.id or existing.password_hash is not None):
        raise HTTPException(status.HTTP_409_CONFLICT, "Пользователь с таким email уже зарегистрирован")
    user = existing or User(
        org_id=org.id, email=normalize_email(email), full_name=full_name, role=role, password_hash=None
    )
    db.add(user)
    db.flush()
    s = get_settings()
    token = _create_user_token(db, user, TokenPurpose.invite, timedelta(days=s.invite_token_ttl_days))
    audit(db, "user.invited", org_id=org.id, user_id=inviter.id, email=user.email, role=role.value)
    db.commit()
    link = f"{s.public_url}/invite/{token}"
    mailer.send(
        user.email,
        f"ТехОценка: приглашение в {org.name}",
        f"Здравствуйте, {full_name}!\n\n{inviter.full_name} приглашает вас в организацию «{org.name}» "
        f"на платформе ТехОценка.\n\nДля регистрации перейдите по ссылке "
        f"(действует {s.invite_token_ttl_days} дн.):\n{link}",
    )
    return user, link


def invitation_info(db: Session, token: str) -> User:
    return _consume_user_token(db, token, TokenPurpose.invite, mark_used=False)


def accept_invitation(db: Session, token: str, password: str, full_name: str | None) -> User:
    user = _consume_user_token(db, token, TokenPurpose.invite, mark_used=True)
    user.password_hash = hash_password(password)
    if full_name:
        user.full_name = full_name
    audit(db, "user.invite_accepted", org_id=user.org_id, user_id=user.id)
    return user
