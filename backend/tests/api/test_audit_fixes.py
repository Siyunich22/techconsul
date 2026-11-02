"""Регрессия по аудиту 2026-11-02: приглашения, сессии, перебор паролей, роли, оркестратор оценки."""

import uuid
from contextlib import nullcontext

from sqlalchemy import select, text

from app.assessment.runner import lock_key, run_items
from app.models import AssessmentRun, ItemStatus, SectionResult
from tests.api.test_assessment import agent_handler, ready_project  # noqa: F401 — фикстура


def _invite(manager, mailer, email: str, expert_id: str | None = None) -> str:
    body = {"email": email, "full_name": "Эксперт"} | ({"expert_id": expert_id} if expert_id else {})
    assert manager.post("/api/v1/org/invitations", json=body).status_code == 201
    return mailer.last_link(email).rsplit("/", 1)[-1]


def test_reinvite_expires_previous_link_and_accepted_link_is_dead(register, new_client, mailer):
    manager = register()
    first = _invite(manager, mailer, "twice@example.kz")
    second = _invite(manager, mailer, "twice@example.kz")
    accept = "/api/v1/auth/invitations/accept"
    assert new_client().post(accept, json={"token": first, "password": "expert-pass-1"}).status_code == 400
    assert new_client().post(accept, json={"token": second, "password": "expert-pass-1"}).status_code == 200
    # ссылка, оставшаяся у руководителя, не позволяет сменить пароль эксперта и войти под ним
    assert new_client().post(accept, json={"token": second, "password": "hijack-pass-1"}).status_code == 400


def test_password_change_revokes_other_sessions(register, new_client):
    c = register()
    other = new_client()
    assert (
        other.post("/api/v1/auth/login", json={"email": c.email, "password": c.password}).status_code == 200
    )
    new = {"current_password": c.password, "new_password": "another-pass-1"}
    assert c.post("/api/v1/me/password", json=new).status_code == 204
    assert c.post("/api/v1/auth/refresh").status_code == 200  # текущее устройство получило новую сессию
    # украденная сессия не живёт (а её предъявление, как повторное использование, гасит все сессии)
    assert other.post("/api/v1/auth/refresh").status_code == 401


def test_login_bruteforce_is_limited(register, new_client):
    c = register()
    attacker = new_client()
    codes = [
        attacker.post("/api/v1/auth/login", json={"email": c.email, "password": "wrong-pass"}).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_platform_bin_cannot_be_registered(new_client):
    resp = new_client().post(
        "/api/v1/auth/register",
        json={
            "organization": {"name": "Захват", "bin": "000000000000"},
            "user": {"full_name": "X", "email": "squat@example.kz", "password": "secret-pass-1"},
        },
    )
    assert resp.status_code == 422


def test_org_settings_save_keeps_web_search_flag(register):
    c = register()
    settings = c.get("/api/v1/org").json()["settings"]
    c.patch("/api/v1/org", json={"settings": {**settings, "web_search_enabled": False}})
    old_form = {k: v for k, v in settings.items() if k != "web_search_enabled"}  # клиент без поля
    c.patch("/api/v1/org", json={"settings": {**old_form, "default_font": "Arial"}})
    saved = c.get("/api/v1/org").json()["settings"]
    assert saved["default_font"] == "Arial" and saved["web_search_enabled"] is False


def test_svg_logo_is_sandboxed(register):
    c = register()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert c.post("/api/v1/org/logo", files={"file": ("logo.svg", svg, "image/svg+xml")}).status_code == 200
    logo = c.get("/api/v1/org/logo")
    assert "sandbox" in logo.headers["content-security-policy"]
    assert logo.headers["x-content-type-options"] == "nosniff"


def test_observer_cannot_read_expert_registry(register, new_client, mailer):
    manager = register()
    body = {"email": "bank@example.kz", "full_name": "Банк", "role": "observer"}
    assert manager.post("/api/v1/org/invitations", json=body).status_code == 201
    token = mailer.last_link("bank@example.kz").rsplit("/", 1)[-1]
    observer = new_client()
    observer.post("/api/v1/auth/invitations/accept", json={"token": token, "password": "observer-pass-1"})
    assert observer.get("/api/v1/org/experts").status_code == 403
    assert manager.get("/api/v1/org/experts").status_code == 200


def test_expert_reruns_only_assigned_sections(ready_project, llm, mailer, new_client):  # noqa: F811
    c, p = ready_project
    llm.agent_handler = agent_handler()
    ex = c.post("/api/v1/org/experts", json={"full_name": "Эксперт 3.2"}).json()
    c.put(f"/api/v1/projects/{p['id']}/members", json=[{"expert_id": ex["id"], "assigned_items": ["3.2"]}])
    token = _invite(c, mailer, "e32@example.kz", ex["id"])
    e = new_client()
    e.post("/api/v1/auth/invitations/accept", json={"token": token, "password": "expert-pass-1"})
    base = f"/api/v1/projects/{p['id']}"
    assert c.post(f"{base}/runs").status_code == 202
    assert e.post(f"{base}/assessment/items/3.4.1/rerun", json={}).status_code == 403
    assert e.post(f"{base}/assessment/items/3.2.1/rerun", json={}).status_code == 202


def _queued_run(c, p, db) -> AssessmentRun:
    """Прогон без исполнения задачи — пункты в pending, как сразу после постановки в очередь."""
    from app.services import assessment as svc

    class NoQueue:
        def run(self, run_id, only=None):
            pass

    from app.api.deps import Principal
    from app.models import Project, User

    user = db.scalar(select(User).where(User.email == c.email))
    return svc.start_run(db, NoQueue(), Principal(user=user), db.get(Project, uuid.UUID(p["id"])))


def test_orphaned_running_items_are_resumed(ready_project, llm, db):  # noqa: F811
    """Воркер погиб посреди пункта: повторная доставка задачи доводит прогон до конца."""
    c, p = ready_project
    llm.agent_handler = agent_handler()
    run = _queued_run(c, p, db)
    run.status = run.status.running
    orphan = db.scalar(
        select(SectionResult).where(SectionResult.run_id == run.id, SectionResult.item_id == "3.4.1")
    )
    orphan.status = ItemStatus.running
    db.commit()
    run_items(lambda: nullcontext(db), run.id, concurrency=1)
    db.refresh(run)
    rows = db.scalars(select(SectionResult).where(SectionResult.run_id == run.id)).all()
    assert run.status.value == "done" and all(r.status == ItemStatus.done for r in rows)


def test_run_held_by_another_worker_is_not_executed_twice(ready_project, llm, db):  # noqa: F811
    c, p = ready_project
    calls: list[str] = []
    llm.agent_handler = agent_handler(calls=calls)
    run = _queued_run(c, p, db)
    engine = db.get_bind().engine
    with engine.connect() as holder:  # другой воркер уже ведёт этот прогон
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": lock_key(run.id)})
        run_items(lambda: nullcontext(db), run.id, concurrency=1)
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key(run.id)})
        holder.commit()
    assert calls == []
    pending = db.scalars(select(SectionResult.status).where(SectionResult.run_id == run.id)).all()
    assert set(pending) == {ItemStatus.pending}


def test_concurrent_start_is_serialized(ready_project, db):  # noqa: F811
    c, p = ready_project
    _queued_run(c, p, db)
    assert c.post(f"/api/v1/projects/{p['id']}/runs").status_code == 409  # прогон в очереди — второй нельзя
