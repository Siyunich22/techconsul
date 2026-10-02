from sqlalchemy import select

from app.core.security import ACCESS_COOKIE, REFRESH_COOKIE
from app.models import AuditLog, RefreshSession, User
from tests.conftest import unique_bin


def test_register_creates_org_and_manager_and_logs_in(register, db):
    c = register(org_name="ТОО «Консалт»")
    me = c.get("/api/v1/me").json()
    assert me["role"] == "manager"
    assert me["organization"]["name"] == "ТОО «Консалт»"
    user = db.scalar(select(User).where(User.email == c.email))
    assert user.password_hash and user.password_hash.startswith("$argon2")
    assert db.scalar(
        select(AuditLog).where(AuditLog.action == "org.register", AuditLog.org_id == user.org_id)
    )


def test_register_sets_httponly_cookies(new_client):
    c = new_client()
    resp = c.post(
        "/api/v1/auth/register",
        json={
            "organization": {"name": "ТОО А", "bin": unique_bin()},
            "user": {"full_name": "А", "email": "cookie@example.kz", "password": "secret-pass-1"},
        },
    )
    cookies = resp.headers.get_list("set-cookie")
    access = next(h for h in cookies if h.startswith(ACCESS_COOKIE))
    refresh = next(h for h in cookies if h.startswith(REFRESH_COOKIE))
    assert "HttpOnly" in access and "HttpOnly" in refresh
    assert "Path=/api/v1/auth" in refresh  # refresh-токен не уходит в обычные запросы


def test_register_rejects_duplicates_and_bad_bin(register, new_client):
    first = register(email="dup@example.kz")
    c = new_client()
    payload = {
        "organization": {"name": "ТОО Б", "bin": unique_bin()},
        "user": {"full_name": "Б", "email": "DUP@example.kz", "password": "secret-pass-1"},
    }
    assert c.post("/api/v1/auth/register", json=payload).status_code == 409
    org_bin = first.get("/api/v1/org").json()["bin"]
    payload["user"]["email"] = "other@example.kz"
    payload["organization"]["bin"] = org_bin
    assert c.post("/api/v1/auth/register", json=payload).status_code == 409
    payload["organization"]["bin"] = "12345"
    assert c.post("/api/v1/auth/register", json=payload).status_code == 422


def test_login_logout(register, new_client):
    reg = register()
    c = new_client()
    bad = c.post("/api/v1/auth/login", json={"email": reg.email, "password": "wrong-password"})
    assert bad.status_code == 401
    assert c.get("/api/v1/me").status_code == 401
    ok = c.post("/api/v1/auth/login", json={"email": reg.email.upper(), "password": reg.password})
    assert ok.status_code == 200
    assert c.get("/api/v1/me").status_code == 200
    assert c.post("/api/v1/auth/logout").status_code == 204
    assert c.get("/api/v1/me").status_code == 401
    assert c.post("/api/v1/auth/refresh").status_code == 401


def test_refresh_rotates_and_detects_reuse(register, db):
    c = register()
    old_refresh = c.cookies.get(REFRESH_COOKIE)
    assert c.post("/api/v1/auth/refresh").status_code == 200
    assert c.cookies.get(REFRESH_COOKIE) != old_refresh

    # Повтор старого (отозванного) токена — отзыв всех сессий пользователя.
    c.cookies.set(REFRESH_COOKIE, old_refresh, path="/api/v1/auth")
    assert c.post("/api/v1/auth/refresh").status_code == 401
    user = db.scalar(select(User).where(User.email == c.email))
    active = db.scalars(
        select(RefreshSession).where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
    ).all()
    assert active == []


def test_forgot_and_reset_password(register, new_client, mailer):
    reg = register()
    c = new_client()
    assert c.post("/api/v1/auth/forgot", json={"email": "nobody@example.kz"}).status_code == 204
    assert mailer.sent == []

    assert c.post("/api/v1/auth/forgot", json={"email": reg.email}).status_code == 204
    token = mailer.last_link(reg.email).rsplit("/", 1)[-1]
    assert c.post("/api/v1/auth/reset", json={"token": token, "password": "new-secret-2"}).status_code == 204
    assert c.post("/api/v1/auth/reset", json={"token": token, "password": "new-secret-3"}).status_code == 400

    assert (
        c.post("/api/v1/auth/login", json={"email": reg.email, "password": reg.password}).status_code == 401
    )
    assert (
        c.post("/api/v1/auth/login", json={"email": reg.email, "password": "new-secret-2"}).status_code == 200
    )
    # старые сессии отозваны
    assert reg.post("/api/v1/auth/refresh").status_code == 401


def test_invite_expert_flow(register, new_client, mailer):
    manager = register()
    expert = manager.post("/api/v1/org/experts", json={"full_name": "Петров П.П."}).json()
    resp = manager.post(
        "/api/v1/org/invitations",
        json={
            "email": "expert@example.kz",
            "full_name": "Петров П.П.",
            "role": "expert",
            "expert_id": expert["id"],
        },
    )
    assert resp.status_code == 201, resp.text
    token = mailer.last_link("expert@example.kz").rsplit("/", 1)[-1]
    assert resp.json()["invite_link"].endswith(token)

    c = new_client()
    info = c.get(f"/api/v1/auth/invitations/{token}").json()
    assert info["organization_name"] == "ТОО Исполнитель"
    me = c.post("/api/v1/auth/invitations/accept", json={"token": token, "password": "expert-pass-1"})
    assert me.status_code == 200 and me.json()["role"] == "expert"
    assert c.get(f"/api/v1/auth/invitations/{token}").status_code == 400

    users = manager.get("/api/v1/org/users").json()
    assert {u["email"]: u["invitation_pending"] for u in users}["expert@example.kz"] is False
    assert manager.get(f"/api/v1/org/experts/{expert['id']}").json()["user_id"] == me.json()["id"]
    # эксперт не управляет организацией
    assert c.post("/api/v1/org/experts", json={"full_name": "X"}).status_code == 403
    assert c.patch("/api/v1/org", json={"name": "X"}).status_code == 403


def test_profile_and_password_change(register):
    c = register()
    me = c.patch("/api/v1/me", json={"position": "Директор", "phone": "+7 701 000 00 00"}).json()
    assert me["position"] == "Директор"
    bad = c.post("/api/v1/me/password", json={"current_password": "nope", "new_password": "another-pass-1"})
    assert bad.status_code == 400
    ok = c.post(
        "/api/v1/me/password", json={"current_password": c.password, "new_password": "another-pass-1"}
    )
    assert ok.status_code == 204
