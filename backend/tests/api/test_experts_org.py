import io

from tests.conftest import pdf_file


def test_expert_crud_and_cv(register, storage):
    c = register()
    ex = c.post(
        "/api/v1/org/experts",
        json={
            "full_name": "Сидоров С.С.",
            "specialization": ["энергетик"],
            "education": "КазНТУ, 2005",
            "research_experience": "12 публикаций",
            "years": 18,
            "email": "s@example.kz",
        },
    ).json()
    assert ex["years"] == 18 and ex["cv_filename"] is None

    up = c.post(f"/api/v1/org/experts/{ex['id']}/cv", files={"file": pdf_file("Резюме Сидоров.pdf")})
    assert up.status_code == 200, up.text
    assert up.json()["cv_filename"] == "Резюме_Сидоров.pdf"
    # повторная загрузка не перезаписывает файл, а создаёт новый ключ
    c.post(f"/api/v1/org/experts/{ex['id']}/cv", files={"file": pdf_file()})
    assert len(storage.objects) == 2

    dl = c.get(f"/api/v1/org/experts/{ex['id']}/cv")
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")

    bad = c.post(f"/api/v1/org/experts/{ex['id']}/cv", files={"file": ("cv.exe", io.BytesIO(b"MZ"), "x")})
    assert bad.status_code == 415
    empty = c.post(f"/api/v1/org/experts/{ex['id']}/cv", files={"file": ("cv.pdf", io.BytesIO(b""), "x")})
    assert empty.status_code == 400

    patched = c.patch(f"/api/v1/org/experts/{ex['id']}", json={"years": 19, "is_active": False}).json()
    assert patched["years"] == 19
    assert c.get("/api/v1/org/experts").json() == []
    assert len(c.get("/api/v1/org/experts", params={"include_inactive": True}).json()) == 1
    assert c.delete(f"/api/v1/org/experts/{ex['id']}").status_code == 204


def test_expert_in_team_cannot_be_deleted(register):
    c = register()
    ex = c.post("/api/v1/org/experts", json={"full_name": "В команде"}).json()
    c.post("/api/v1/projects", json={"name": "П", "customer_name": "З", "members": [{"expert_id": ex["id"]}]})
    assert c.delete(f"/api/v1/org/experts/{ex['id']}").status_code == 409


def test_org_profile_settings_and_files(register):
    c = register()
    org = c.patch(
        "/api/v1/org",
        json={
            "address": "г. Астана, пр. Мангилик Ел, 1",
            "phone": "+7 7172 000000",
            "settings": {"default_font": "Arial", "page_number_format": "page_of_total"},
        },
    ).json()
    assert org["address"].startswith("г. Астана")
    assert org["settings"]["default_font"] == "Arial" and org["settings"]["report_language"] == "ru"

    assert c.get("/api/v1/org/logo").status_code == 404
    logo = c.post("/api/v1/org/logo", files={"file": ("logo.png", io.BytesIO(b"\x89PNG..."), "image/png")})
    assert logo.json()["has_logo"] is True
    assert c.get("/api/v1/org/logo").content.startswith(b"\x89PNG")

    docx = c.post("/api/v1/org/reference-docx", files={"file": ("ref.docx", io.BytesIO(b"PK\x03\x04"), "x")})
    assert docx.json()["has_reference_docx"] is True
    assert c.post("/api/v1/org/reference-docx", files={"file": pdf_file()}).status_code == 415
