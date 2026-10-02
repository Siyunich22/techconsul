from datetime import date, timedelta

BASE = {"name": "Мукомольный завод", "customer_name": "ТОО «Агро»"}


def _expert(c, name="Технолог"):
    return c.post("/api/v1/org/experts", json={"full_name": name, "specialization": ["технолог"]}).json()


def test_create_project_full_wizard_payload(register):
    c = register()
    ex = _expert(c)
    resp = c.post(
        "/api/v1/projects",
        json={
            **BASE,
            "customer_bin": "123456789012",
            "industry": "C",
            "region": "Акмолинская область",
            "site": "г. Кокшетау, промзона",
            "capacity_text": "300 т/сут",
            "budget_amount": "12500000000.00",
            "currency": "KZT",
            "bank_name": "АО «БРК»",
            "independence": {
                "affiliated": False,
                "participated_in_docs": False,
                "is_supplier": False,
                "joint_experience": "Совместной деятельности не было",
            },
            "members": [{"expert_id": ex["id"], "role": "lead", "assigned_items": ["3.3", "3.4"]}],
        },
    )
    assert resp.status_code == 201, resp.text
    p = resp.json()
    assert p["status"] == "draft"
    assert p["template_version"]["code"] == "tech_assessment_bank_v1"
    assert p["independence"]["is_complete"] is True
    assert p["members"][0]["assigned_items"] == ["3.3", "3.4"]
    assert p["owner"]["full_name"] == "Иванов Иван"


def test_independence_conflict_requires_justification(register):
    c = register()
    p = c.post(
        "/api/v1/projects",
        json={
            **BASE,
            "independence": {"affiliated": True, "participated_in_docs": False, "is_supplier": False},
        },
    ).json()
    assert p["independence"]["has_conflict"] is True
    assert p["independence"]["is_complete"] is False

    p = c.patch(
        f"/api/v1/projects/{p['id']}",
        json={
            "independence": {
                "affiliated": True,
                "participated_in_docs": False,
                "is_supplier": False,
                "justification": "Аффилированность через общего учредителя прекращена 01.2026",
            }
        },
    ).json()
    assert p["independence"]["is_complete"] is True

    unanswered = c.post("/api/v1/projects", json=BASE).json()
    assert unanswered["independence"]["is_complete"] is False


def test_validation_errors(register):
    c = register()
    assert c.post("/api/v1/projects", json={"name": " ", "customer_name": "x"}).status_code == 422
    assert c.post("/api/v1/projects", json={**BASE, "industry": "ZZ"}).status_code == 422
    assert c.post("/api/v1/projects", json={**BASE, "currency": "XXX"}).status_code == 422
    assert c.post("/api/v1/projects", json={**BASE, "customer_bin": "12"}).status_code == 422
    assert c.post("/api/v1/projects", json={**BASE, "budget_amount": -1}).status_code == 422


def test_member_sections_come_from_template(register):
    c = register()
    ex = _expert(c)
    tpl = c.get("/api/v1/templates").json()[0]
    sections = {s["id"]: s for s in c.get(f"/api/v1/templates/{tpl['id']}/sections").json()}
    assert sections["3.6"]["optional"] is True

    bad = c.post(
        "/api/v1/projects", json={**BASE, "members": [{"expert_id": ex["id"], "assigned_items": ["9.9"]}]}
    )
    assert bad.status_code == 422
    disabled = c.post(
        "/api/v1/projects", json={**BASE, "members": [{"expert_id": ex["id"], "assigned_items": ["3.6"]}]}
    )
    assert disabled.status_code == 422
    enabled = c.post(
        "/api/v1/projects",
        json={
            **BASE,
            "enabled_optional_items": ["3.6"],
            "members": [{"expert_id": ex["id"], "assigned_items": ["3.6"]}],
        },
    )
    assert enabled.status_code == 201, enabled.text
    assert c.post("/api/v1/projects", json={**BASE, "enabled_optional_items": ["3.3"]}).status_code == 422
    dup = c.post(
        "/api/v1/projects", json={**BASE, "members": [{"expert_id": ex["id"]}, {"expert_id": ex["id"]}]}
    )
    assert dup.status_code == 422


def test_update_team(register):
    c = register()
    e1, e2 = _expert(c, "Первый"), _expert(c, "Второй")
    p = c.post("/api/v1/projects", json={**BASE, "members": [{"expert_id": e1["id"]}]}).json()
    members = c.put(
        f"/api/v1/projects/{p['id']}/members",
        json=[{"expert_id": e2["id"], "role": "lead", "assigned_items": ["4"]}],
    ).json()
    assert [m["expert"]["full_name"] for m in members] == ["Второй"]
    assert members[0]["assigned_items"] == ["4"]


def test_list_filters_search_pagination_sort(register):
    c = register()
    for i, (industry, region) in enumerate([("C", "г. Астана"), ("C", "г. Алматы"), ("D", "г. Астана")]):
        c.post(
            "/api/v1/projects",
            json={
                "name": f"Проект {i}",
                "customer_name": f"Заказчик {i}",
                "industry": industry,
                "region": region,
                "budget_amount": i * 100,
            },
        )
    get = lambda **params: c.get("/api/v1/projects", params=params).json()  # noqa: E731

    assert get()["total"] == 3
    assert get(industry="C")["total"] == 2
    assert get(industry="C", region="г. Астана")["total"] == 1
    assert get(q="заказчик 2")["total"] == 1  # поиск по Заказчику, без учёта регистра
    assert get(status="draft")["total"] == 3
    assert get(status="released")["total"] == 0
    page = get(page_size=2, page=2, sort="budget_amount", desc=False)
    assert page["total"] == 3 and [p["name"] for p in page["items"]] == ["Проект 2"]
    today = date.today()
    assert get(updated_from=str(today - timedelta(days=1)), updated_to=str(today))["total"] == 3
    assert get(updated_from=str(today + timedelta(days=1)))["total"] == 0
    assert c.get("/api/v1/projects", params={"sort": "password"}).status_code == 422


def test_archive_unarchive_delete(register):
    c = register()
    p = c.post("/api/v1/projects", json=BASE).json()
    archived = c.post(f"/api/v1/projects/{p['id']}/archive").json()
    assert archived["status"] == "archived"
    assert c.get("/api/v1/projects").json()["total"] == 0  # архив скрыт по умолчанию
    assert c.get("/api/v1/projects", params={"include_archived": True}).json()["total"] == 1
    assert c.delete(f"/api/v1/projects/{p['id']}").status_code == 409  # удаляется только черновик
    assert c.post(f"/api/v1/projects/{p['id']}/unarchive").json()["status"] == "draft"
    assert c.delete(f"/api/v1/projects/{p['id']}").status_code == 204
    assert c.get(f"/api/v1/projects/{p['id']}").status_code == 404


def test_expert_sees_only_own_projects_and_cannot_edit(register, new_client, mailer):
    m = register()
    ex = _expert(m, "Эксперт")
    mine = m.post(
        "/api/v1/projects", json={**BASE, "name": "С экспертом", "members": [{"expert_id": ex["id"]}]}
    ).json()
    m.post("/api/v1/projects", json={**BASE, "name": "Без эксперта"})
    m.post(
        "/api/v1/org/invitations",
        json={"email": "e@example.kz", "full_name": "Эксперт", "role": "expert", "expert_id": ex["id"]},
    )
    token = mailer.last_link("e@example.kz").rsplit("/", 1)[-1]
    e = new_client()
    e.post("/api/v1/auth/invitations/accept", json={"token": token, "password": "expert-pass-1"})

    assert [p["name"] for p in e.get("/api/v1/projects").json()["items"]] == ["С экспертом"]
    assert e.get(f"/api/v1/projects/{mine['id']}").status_code == 200
    assert e.patch(f"/api/v1/projects/{mine['id']}", json={"name": "x"}).status_code == 403
    assert e.post("/api/v1/projects", json=BASE).status_code == 403


def test_reference_requires_auth(client, register):
    assert client.get("/api/v1/reference").status_code == 401
    ref = register().get("/api/v1/reference").json()
    assert "KZT" in ref["currencies"] and any(o["value"] == "C" for o in ref["industries"])
