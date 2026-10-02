"""Тест на утечку данных между организациями (TZ §2, CLAUDE.md правило 4)."""

from app.core.db import Base
from tests.conftest import pdf_file

PROJECT = {"name": "Мукомольный завод 300 т/сут", "customer_name": "ТОО «Агро»", "industry": "C"}


def _setup_two_orgs(register):
    a, b = register(org_name="ТОО Альфа"), register(org_name="ТОО Бета")
    a_expert = a.post("/api/v1/org/experts", json={"full_name": "Эксперт Альфы"}).json()
    a.post(f"/api/v1/org/experts/{a_expert['id']}/cv", files={"file": pdf_file()})
    a_project = a.post(
        "/api/v1/projects",
        json={**PROJECT, "members": [{"expert_id": a_expert["id"], "assigned_items": ["3.3"]}]},
    ).json()
    b_project = b.post("/api/v1/projects", json={**PROJECT, "name": "Проект Беты"}).json()
    return a, b, a_expert, a_project, b_project


def test_projects_not_visible_across_orgs(register):
    a, b, _, a_project, b_project = _setup_two_orgs(register)
    pid = a_project["id"]

    listed = b.get("/api/v1/projects", params={"q": "Мукомольный"}).json()
    assert [p["id"] for p in listed["items"]] == []
    assert {p["id"] for p in b.get("/api/v1/projects").json()["items"]} == {b_project["id"]}

    assert b.get(f"/api/v1/projects/{pid}").status_code == 404
    assert b.patch(f"/api/v1/projects/{pid}", json={"name": "взлом"}).status_code == 404
    assert b.delete(f"/api/v1/projects/{pid}").status_code == 404
    assert b.post(f"/api/v1/projects/{pid}/archive").status_code == 404
    assert b.get(f"/api/v1/projects/{pid}/members").status_code == 404
    assert b.put(f"/api/v1/projects/{pid}/members", json=[]).status_code == 404

    # у владельца ничего не изменилось
    assert a.get(f"/api/v1/projects/{pid}").json()["name"] == PROJECT["name"]
    assert len(a.get(f"/api/v1/projects/{pid}/members").json()) == 1


def test_experts_not_visible_across_orgs(register):
    a, b, a_expert, _, b_project = _setup_two_orgs(register)
    eid = a_expert["id"]

    assert b.get("/api/v1/org/experts").json() == []
    assert b.get(f"/api/v1/org/experts/{eid}").status_code == 404
    assert b.patch(f"/api/v1/org/experts/{eid}", json={"full_name": "x"}).status_code == 404
    assert b.delete(f"/api/v1/org/experts/{eid}").status_code == 404
    assert b.get(f"/api/v1/org/experts/{eid}/cv").status_code == 404
    assert b.post(f"/api/v1/org/experts/{eid}/cv", files={"file": pdf_file()}).status_code == 404

    # чужого эксперта нельзя включить в свою команду или привязать к приглашению
    put = b.put(f"/api/v1/projects/{b_project['id']}/members", json=[{"expert_id": eid}])
    assert put.status_code == 404
    create = b.post("/api/v1/projects", json={**PROJECT, "members": [{"expert_id": eid}]})
    assert create.status_code == 404
    invite = b.post(
        "/api/v1/org/invitations",
        json={"email": "x@example.kz", "full_name": "X", "role": "expert", "expert_id": eid},
    )
    assert invite.status_code == 404


def test_org_and_users_are_own(register):
    a, b, *_ = _setup_two_orgs(register)
    assert a.get("/api/v1/org").json()["name"] == "ТОО Альфа"
    assert b.get("/api/v1/org").json()["name"] == "ТОО Бета"
    a_emails = {u["email"] for u in a.get("/api/v1/org/users").json()}
    b_emails = {u["email"] for u in b.get("/api/v1/org/users").json()}
    assert a_emails == {a.email} and b_emails == {b.email}


def test_every_tenant_table_has_org_id():
    """Все таблицы с данными организаций обязаны иметь org_id (или принадлежать проекту/пользователю)."""
    global_tables = {"template_versions"}  # глобальный справочник платформы
    via_parent = {"project_members", "refresh_sessions", "user_tokens"}  # FK на project/user с org_id
    for name, table in Base.metadata.tables.items():
        if name in global_tables | via_parent:
            continue
        if name == "organizations":
            continue
        assert "org_id" in table.columns, f"таблица {name} без org_id"
