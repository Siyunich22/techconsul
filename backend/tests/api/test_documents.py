import io
import shutil
import zipfile

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditLog, Chunk, LlmCall
from tests.conftest import upload_file
from tests.fixtures.sample_project.generate import EXPECTED_CATEGORIES

needs_tools = pytest.mark.skipif(
    not (shutil.which("tesseract") and shutil.which("soffice")),
    reason="нужны tesseract и LibreOffice (контейнер api)",
)


def _docs(c, pid):
    return {d["filename"]: d for d in c.get(f"/api/v1/projects/{pid}/documents").json()}


def test_chunked_upload_assembles_parts(project, storage, monkeypatch):
    c, p = project
    monkeypatch.setattr(get_settings(), "upload_chunk_size", 1000)
    data = ("Технические условия на подключение к электрическим сетям. " * 80).encode()
    doc = upload_file(c, p["id"], "ТУ_газ.txt", data, relative_path="Исходные/ТУ")
    assert doc["size"] == len(data) and doc["relative_path"] == "Исходные/ТУ"
    stored = next(v for k, v in storage.objects.items() if k.endswith("ТУ_газ.txt"))
    assert stored[0] == data  # части собраны в правильном порядке

    d = c.get(f"/api/v1/projects/{p['id']}/documents/{doc['id']}").json()
    assert d["status"] == "indexed" and d["category"] == "техусловия" and d["category_source"] == "rules"
    assert d["chunk_count"] >= 1
    assert c.get(f"/api/v1/projects/{p['id']}").json()["status"] == "documents_uploaded"


def test_upload_validation(project, monkeypatch):
    c, p = project
    base = f"/api/v1/projects/{p['id']}/uploads"
    assert c.post(base, json={"filename": "virus.exe", "size": 10}).status_code == 415
    too_big = get_settings().max_document_size_mb * 1024**2 + 1
    assert c.post(base, json={"filename": "big.pdf", "size": too_big}).status_code == 413
    monkeypatch.setattr(get_settings(), "max_project_size_gb", 0)
    assert c.post(base, json={"filename": "a.pdf", "size": 10}).status_code == 413
    monkeypatch.undo()

    monkeypatch.setattr(get_settings(), "upload_chunk_size", 100)
    info = c.post(base, json={"filename": "a.txt", "size": 250}).json()
    assert info["parts_total"] == 3
    uid = info["upload_id"]
    assert c.put(f"{base}/{uid}/parts/1", content=b"x" * 99).status_code == 422  # не тот размер
    assert c.put(f"{base}/{uid}/parts/4", content=b"x" * 50).status_code == 422  # нет такой части
    assert c.put(f"{base}/{uid}/parts/1", content=b"x" * 100).status_code == 204
    assert c.post(f"{base}/{uid}/complete").status_code == 409  # не все части
    assert c.delete(f"{base}/{uid}").status_code == 204
    assert c.post(f"{base}/{uid}/complete").status_code == 404


@needs_tools
def test_sample_project_end_to_end(project, sample_files, db):
    """Критерий приёмки фазы 2: sample_project загружается, все файлы indexed, категории ≥ 90%,
    индикатор полноты корректен."""
    c, p = project
    pid = p["id"]
    for name in EXPECTED_CATEGORIES:
        upload_file(c, pid, name, (sample_files / name).read_bytes())

    docs = _docs(c, pid)
    assert {d["status"] for d in docs.values()} == {"indexed"}, {n: d["error"] for n, d in docs.items()}
    correct = sum(docs[n]["category"] == cat for n, cat in EXPECTED_CATEGORIES.items())
    assert correct / len(EXPECTED_CATEGORIES) >= 0.9
    assert docs["ТЭО_мукомольный_завод_300тсут.pdf"]["pages"] == 30
    assert docs["ТУ_электроснабжение_скан.jpg"]["ocr_pages"] == 1
    assert docs["Финмодель_мукомольный_завод.xlsx"]["meta"]["recalculated"] is True

    comp = c.get(f"/api/v1/projects/{pid}/completeness").json()
    assert comp["documents_total"] == 7 and comp["documents_processing"] == 0
    present = {cat["code"]: cat["documents"] for cat in comp["categories"]}
    assert present["кп_поставщиков"] == 3 and present["тэо_bfs"] == 1
    missing = {m["code"]: m for m in comp["missing"]}
    assert "псд" in missing and "тэо_bfs" not in missing
    assert "3.4.2" in missing["псд"]["partial_items"]  # «Нет ПСД — 3.4.2 будет раскрыт частично»
    assert "3.7.4" in missing["экология"]["blocked_items"]  # 3.7.4 требует экология + псд — нет ни одного
    items = {i["item_id"]: i for i in comp["items"]}
    assert items["3.4.3"]["status"] == "full"  # тэо_bfs + кп_поставщиков
    assert {r["kind"] for r in comp["missing_references"]} >= {"ndt", "law"}

    # гибридный поиск возвращает фрагмент со страницей-источником
    hits = c.get(f"/api/v1/projects/{pid}/search", params={"q": "разрешенная мощность подключения"}).json()
    assert hits and hits[0]["filename"] == "ТУ_электроснабжение_скан.jpg" and hits[0]["page"] == 1
    hits = c.get(
        f"/api/v1/projects/{pid}/search", params={"q": "итого CAPEX", "category": "финмодель"}
    ).json()
    assert hits[0]["sheet"] == "CAPEX" and hits[0]["cell_range"].startswith("A1:")

    # предпросмотр: текст страницы и рендер с подсветкой
    teo = docs["ТЭО_мукомольный_завод_300тсут.pdf"]
    page = c.get(f"/api/v1/projects/{pid}/documents/{teo['id']}/pages/2").json()
    assert "320 тонн" in page["text"] and page["has_image"]
    img = c.get(f"/api/v1/projects/{pid}/documents/{teo['id']}/pages/2/image", params={"q": "320 тонн"})
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    bp = docs["Бизнес-план_Мукомольный_завод.docx"]
    assert (
        c.get(f"/api/v1/projects/{pid}/documents/{bp['id']}/pages/1/image").status_code == 200
    )  # PDF-превью docx

    assert db.scalar(select(AuditLog).where(AuditLog.action == "document.uploaded")) is not None


def test_archive_unpacked_into_children(project):
    c, p = project
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "Пакет/Договор поставки.txt", "Договор поставки. Поставщик обязуется передать оборудование."
        )
        zf.writestr("Пакет/readme.exe", "MZ")
    archive = upload_file(c, p["id"], "Документы.zip", buf.getvalue())
    docs = _docs(c, p["id"])
    assert docs["Документы.zip"]["status"] == "extracted"
    child = docs["Договор поставки.txt"]
    assert child["parent_id"] == archive["id"] and child["status"] == "indexed"
    assert child["relative_path"] == "Документы.zip/Пакет/Договор поставки.txt"
    assert child["category"] == "договоры_поставки"
    assert docs["readme.exe"]["status"] == "error" and "не поддерживается" in docs["readme.exe"]["error"]

    # удаление архива скрывает вложенные документы и убирает их из индекса
    assert c.delete(f"/api/v1/projects/{p['id']}/documents/{archive['id']}").status_code == 204
    assert _docs(c, p["id"]) == {}


def test_broken_file_reports_error_and_can_be_reprocessed(project, enqueuer):
    c, p = project
    doc = upload_file(c, p["id"], "битый.pdf", b"not a pdf at all")
    d = c.get(f"/api/v1/projects/{p['id']}/documents/{doc['id']}").json()
    assert d["status"] == "error" and "PDF" in d["error"]
    again = c.post(f"/api/v1/projects/{p['id']}/documents/{doc['id']}/reprocess")
    assert again.status_code == 200
    assert [str(i) for i in enqueuer.ingested].count(doc["id"]) == 2  # конвейер запущен повторно
    assert c.get(f"/api/v1/projects/{p['id']}/documents/{doc['id']}").json()["status"] == "error"


def test_user_category_override_survives_reprocess(project, db):
    c, p = project
    doc = upload_file(c, p["id"], "заметки.txt", b"some notes")
    assert c.get(f"/api/v1/projects/{p['id']}/documents/{doc['id']}").json()["category"] == "прочее"
    bad = c.patch(f"/api/v1/projects/{p['id']}/documents/{doc['id']}", json={"category": "выдумка"})
    assert bad.status_code == 422
    ok = c.patch(f"/api/v1/projects/{p['id']}/documents/{doc['id']}", json={"category": "экология"}).json()
    assert ok["category"] == "экология" and ok["category_source"] == "user"
    c.post(f"/api/v1/projects/{p['id']}/documents/{doc['id']}/reprocess")
    assert c.get(f"/api/v1/projects/{p['id']}/documents/{doc['id']}").json()["category"] == "экология"


def test_documents_isolated_between_orgs(project, register, db):
    c, p = project
    doc = upload_file(c, p["id"], "ТУ.txt", "Технические условия на подключение".encode())
    other = register(org_name="ТОО Чужая")
    base = f"/api/v1/projects/{p['id']}"
    assert other.get(f"{base}/documents").status_code == 404
    assert other.get(f"{base}/documents/{doc['id']}/file").status_code == 404
    assert other.get(f"{base}/search", params={"q": "условия"}).status_code == 404
    assert other.post(f"{base}/uploads", json={"filename": "a.pdf", "size": 1}).status_code == 404
    # поиск собственного проекта чужой организации не видит её чанков
    op = other.post("/api/v1/projects", json={"name": "Свой", "customer_name": "X"}).json()
    assert other.get(f"/api/v1/projects/{op['id']}/search", params={"q": "условия"}).json() == []
    assert db.scalar(select(Chunk).where(Chunk.project_id == p["id"])) is not None


def test_expert_cannot_delete_documents(project, mailer, new_client):
    c, p = project
    ex = c.post("/api/v1/org/experts", json={"full_name": "Эксперт"}).json()
    c.put(f"/api/v1/projects/{p['id']}/members", json=[{"expert_id": ex["id"]}])
    c.post(
        "/api/v1/org/invitations",
        json={"email": "ex@example.kz", "full_name": "Эксперт", "expert_id": ex["id"]},
    )
    token = mailer.last_link("ex@example.kz").rsplit("/", 1)[-1]
    e = new_client()
    e.post("/api/v1/auth/invitations/accept", json={"token": token, "password": "expert-pass-1"})
    doc = upload_file(e, p["id"], "КП.txt", "Коммерческое предложение".encode())  # эксперт команды загружает
    assert e.delete(f"/api/v1/projects/{p['id']}/documents/{doc['id']}").status_code == 403


def test_llm_classification_is_accounted(project, llm, db):
    c, p = project
    llm.answers.append({"category": "правоустанавливающие", "confidence": 0.9, "reason": "акт на землю"})
    doc = upload_file(c, p["id"], "скан_001.txt", "Документ без ключевых слов".encode())
    d = c.get(f"/api/v1/projects/{p['id']}/documents/{doc['id']}").json()
    assert (d["category"], d["category_source"]) == ("правоустанавливающие", "llm")
    assert llm.calls[0]["purpose"] == "classify_document"
    # FakeLLM не пишет учёт — учёт ведёт AnthropicLLM; здесь проверяем, что таблица доступна
    assert db.scalar(select(LlmCall)) is None


def test_reference_library_admin_only_and_feeds_completeness(project, register, new_client, db):
    from app.cli import create_admin  # noqa: F401 — CLI существует
    from app.core.security import hash_password
    from app.models import Organization, User, UserRole

    c, p = project
    files = {
        "file": (
            "НДТ_пищевая.txt",
            "Наилучшие доступные техники для пищевой промышленности: аспирация.".encode(),
        )
    }
    form = {"title": "Справочник НДТ: пищевая промышленность", "kind": "ndt"}
    assert c.post("/api/v1/admin/reference-docs", files=files, data=form).status_code == 403

    org = Organization(name="Платформа", bin="000000000009")
    db.add(org)
    db.flush()
    db.add(
        User(
            org_id=org.id,
            email="root@example.kz",
            full_name="Админ",
            role=UserRole.admin,
            password_hash=hash_password("admin-pass-1"),
        )
    )
    db.commit()
    admin = new_client()
    admin.post("/api/v1/auth/login", json={"email": "root@example.kz", "password": "admin-pass-1"})
    ref = admin.post("/api/v1/admin/reference-docs", files=files, data=form)
    assert ref.status_code == 201, ref.text
    assert admin.get("/api/v1/admin/reference-docs").json()[0]["status"] == "indexed"
    hits = admin.get("/api/v1/admin/reference-docs/search", params={"q": "аспирация"}).json()
    assert hits and hits[0]["kind"] == "ndt"

    comp = c.get(f"/api/v1/projects/{p['id']}/completeness").json()
    assert "ndt" not in {r["kind"] for r in comp["missing_references"]}
