import pytest

from app.core.security import hash_password
from app.models import Organization, User, UserRole
from app.template_engine.loader import template_path

YAML_TEXT = template_path("tech_assessment_bank_v1").read_text(encoding="utf-8")
V2 = YAML_TEXT.replace("code: tech_assessment_bank_v1", "code: tech_assessment_bank_v2", 1).replace(
    'title: "Оценка технической проработанности и закупок инвестиционного проекта"',
    'title: "Оценка технической проработанности (фонд, ред. 2027)"',
    1,
)


@pytest.fixture
def admin(db, new_client):
    org = Organization(name="Платформа", bin="000000000008")
    db.add(org)
    db.flush()
    db.add(
        User(
            org_id=org.id,
            email="tpl-admin@example.kz",
            full_name="Админ",
            role=UserRole.admin,
            password_hash=hash_password("admin-pass-1"),
        )
    )
    db.commit()
    c = new_client()
    assert c.post(
        "/api/v1/auth/login", json={"email": "tpl-admin@example.kz", "password": "admin-pass-1"}
    ).is_success
    return c


def _upload(c, text, **form):
    return c.post("/api/v1/admin/templates", files={"file": ("t.yaml", text.encode())}, data=form)


def test_only_admin_manages_templates(register):
    c = register()
    assert c.get("/api/v1/admin/templates").status_code == 403
    assert _upload(c, V2).status_code == 403


def test_default_template_listed_with_summary(admin):
    rows = admin.get("/api/v1/admin/templates").json()
    v1 = next(r for r in rows if r["code"] == "tech_assessment_bank_v1")
    assert v1["is_default"] and v1["summary"]["mandatory_risks"] == 15
    assert v1["summary"]["categories"] == 13 and v1["summary"]["items_optional"] == 2
    assert len(v1["warnings"]) == 5


def test_validate_dry_run(admin):
    ok = admin.post("/api/v1/admin/templates/validate", data={"yaml_text": V2}).json()
    assert ok["valid"] and ok["code"] == "tech_assessment_bank_v2"
    broken = V2.replace("kind: analysis", "kind: analysys", 1)
    bad = admin.post("/api/v1/admin/templates/validate", files={"file": ("t.yaml", broken.encode())}).json()
    assert not bad["valid"]
    assert any(i["level"] == "error" and "analysys" in i["message"] for i in bad["issues"])
    assert admin.post("/api/v1/admin/templates/validate").status_code == 422


def test_upload_rejects_invalid_and_duplicate(admin):
    bad = _upload(admin, V2.replace("inputs: [тэо_bfs, кп_поставщиков]", "inputs: [тэо_bfs, чертежи]", 1))
    assert bad.status_code == 422
    assert "чертежи" in str(bad.json()["detail"]["issues"])
    assert _upload(admin, YAML_TEXT).status_code == 409  # код v1 уже есть — версии неизменяемы
    assert _upload(admin, "::: not yaml").status_code == 422


def test_new_default_applies_to_new_projects_only(admin, register):
    manager = register()
    old = manager.post("/api/v1/projects", json={"name": "Старый", "customer_name": "А"}).json()
    assert old["template_version"]["code"] == "tech_assessment_bank_v1"

    v2 = _upload(admin, V2, make_default="true")
    assert v2.status_code == 201, v2.text
    assert v2.json()["is_default"]
    new = manager.post("/api/v1/projects", json={"name": "Новый", "customer_name": "Б"}).json()
    assert new["template_version"]["code"] == "tech_assessment_bank_v2"
    # проект фиксирует версию шаблона при создании
    assert (
        manager.get(f"/api/v1/projects/{old['id']}").json()["template_version"]["code"]
        == "tech_assessment_bank_v1"
    )

    rows = {r["code"]: r for r in admin.get("/api/v1/admin/templates").json()}
    assert (
        not rows["tech_assessment_bank_v1"]["is_default"] and rows["tech_assessment_bank_v2"]["projects"] == 1
    )
    # удалить шаблон по умолчанию или используемый — нельзя
    assert admin.delete(f"/api/v1/admin/templates/{rows['tech_assessment_bank_v2']['id']}").status_code == 409
    assert admin.delete(f"/api/v1/admin/templates/{rows['tech_assessment_bank_v1']['id']}").status_code == 409


def test_unused_template_can_be_deleted(admin):
    tid = _upload(admin, V2).json()["id"]
    assert admin.delete(f"/api/v1/admin/templates/{tid}").status_code == 204
    assert admin.get(f"/api/v1/templates/{tid}").status_code == 404


def test_template_detail_tree_and_yaml(register):
    c = register()
    tpl = c.get("/api/v1/templates").json()[0]
    detail = c.get(f"/api/v1/templates/{tpl['id']}").json()
    assert [n["id"] for n in detail["tree"]] == ["1", "2", "3", "4", "5", "6"]
    assert detail["document_categories"]["псд"] == "Проектно-сметная документация"
    item = detail["tree"][2]["children"][3]["children"][0]
    assert item["id"] == "3.4.1" and item["inputs"] == [
        "тэо_bfs",
        "псд",
        "кп_поставщиков",
        "договоры_поставки",
    ]
    raw = c.get(f"/api/v1/templates/{tpl['id']}/yaml")
    assert raw.status_code == 200 and raw.text == YAML_TEXT


def test_project_items_respect_optional_flag(register):
    c = register()
    p = c.post("/api/v1/projects", json={"name": "П", "customer_name": "З"}).json()
    s36 = c.get(f"/api/v1/projects/{p['id']}/items").json()[2]["children"][5]
    assert s36["id"] == "3.6" and s36["enabled"] is False
    c.patch(f"/api/v1/projects/{p['id']}", json={"enabled_optional_items": ["3.6"]})
    s36 = c.get(f"/api/v1/projects/{p['id']}/items").json()[2]["children"][5]
    assert s36["enabled"] is True and s36["children"][0]["enabled"] is True
    other = register(org_name="Чужая")
    assert other.get(f"/api/v1/projects/{p['id']}/items").status_code == 404
