"""make seed — демо-организация, администратор платформы, sample_project с документами. Идемпотентно.

Запуск: python -m app.seed  (в контейнере api). Документы sample_project обрабатываются воркерами Celery.
"""

from decimal import Decimal

from sqlalchemy import select

from app.api.deps import Principal
from app.cli import create_admin
from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.core.storage import get_storage, make_key
from app.models import Document, DocumentStatus, Expert, Organization, Project, User, UserRole
from app.pipeline.ingest import mime_for
from app.schemas.project import Independence, MemberIn, ProjectCreate
from app.services.projects import create_project
from app.template_engine.loader import ensure_default_template

DEMO_BIN = "000000000001"
DEMO_EMAIL = "demo@techocenka.kz"
DEMO_PASSWORD = "demo-pass-1"
ADMIN_EMAIL = "admin@techocenka.kz"
ADMIN_PASSWORD = "admin-pass-1"

EXPERTS = [
    ("Ахметов Ержан Серикович", ["технолог"], "КазНТУ им. Сатпаева, инженер-технолог, 2004", 20),
    ("Ким Ольга Викторовна", ["энергетик"], "АУЭС, электроэнергетика, 2008", 16),
    ("Нурланова Айгерим Болатовна", ["эколог"], "КазНУ им. аль-Фараби, экология, 2011", 13),
    ("Петров Сергей Иванович", ["сметчик/закупки"], "КазГАСА, экономика строительства, 2006", 18),
]

PROJECTS = [
    dict(
        name="Мукомольный завод 300 т/сут",
        customer_name="ТОО «Агро Инвест Кокшетау»",
        industry="C",
        region="Акмолинская область",
        capacity_text="300 т/сут зерна",
        budget_amount=Decimal("12500000000"),
        bank_name="АО «Банк Развития Казахстана»",
        site="г. Кокшетау, промзона",
    ),
    dict(
        name="Солнечная электростанция 50 МВт",
        customer_name="ТОО «Сункар Энерджи»",
        industry="D",
        region="Туркестанская область",
        capacity_text="50 МВт",
        budget_amount=Decimal("27000000000"),
        bank_name="АО «Банк Развития Казахстана»",
    ),
    dict(
        name="Завод по переработке молока 100 т/сут",
        customer_name="ТОО «Ак Сут»",
        industry="C",
        region="Северо-Казахстанская область",
        capacity_text="100 т/сут молока",
        budget_amount=Decimal("6800000000"),
        bank_name="АО «Фонд развития предпринимательства «Даму»",
    ),
]


def run() -> None:
    create_admin(ADMIN_EMAIL, ADMIN_PASSWORD)
    with get_sessionmaker()() as db:
        ensure_default_template(db)
        if not db.scalar(select(Organization).where(Organization.bin == DEMO_BIN)):
            _create_demo(db)
        _upload_sample_project(db)
    print(
        f"Вход руководителя: {DEMO_EMAIL} / {DEMO_PASSWORD}; администратор: {ADMIN_EMAIL} / {ADMIN_PASSWORD}"
    )


def _upload_sample_project(db) -> None:
    """Файлы sample_project → первый демо-проект; обработка идёт в воркерах (статусы видны в UI)."""
    from tests.fixtures.sample_project.generate import EXPECTED_CATEGORIES, FILES_DIR, generate
    from workers.tasks import enqueue_ingest

    org = db.scalar(select(Organization).where(Organization.bin == DEMO_BIN))
    project = db.scalar(select(Project).where(Project.org_id == org.id, Project.name == PROJECTS[0]["name"]))
    if project is None or db.scalar(select(Document.id).where(Document.project_id == project.id).limit(1)):
        return
    if not all((FILES_DIR / name).exists() for name in EXPECTED_CATEGORIES):
        generate(FILES_DIR)
    storage = get_storage()
    owner = db.get(User, project.owner_id)
    for name in EXPECTED_CATEGORIES:
        path = FILES_DIR / name
        key = make_key("orgs", str(org.id), "projects", str(project.id), "documents", filename=name)
        with open(path, "rb") as f:
            storage.put(key, f, mime_for(name))
        doc = Document(
            org_id=org.id,
            project_id=project.id,
            filename=name,
            mime=mime_for(name),
            size=path.stat().st_size,
            file_key=key,
            uploaded_by=owner.id,
            status=DocumentStatus.uploaded,
        )
        db.add(doc)
        db.commit()
        enqueue_ingest(doc.id)
    print(f"sample_project: {len(EXPECTED_CATEGORIES)} файлов поставлены в обработку")


def _create_demo(db) -> None:
    org = Organization(name="ТОО «Демо Консалт»", bin=DEMO_BIN, address="г. Астана, пр. Мангилик Ел, 55")
    db.add(org)
    db.flush()
    user = User(
        org_id=org.id,
        email=DEMO_EMAIL,
        password_hash=hash_password(DEMO_PASSWORD),
        full_name="Демо Руководитель",
        role=UserRole.manager,
        position="Директор",
    )
    db.add(user)
    experts = [
        Expert(org_id=org.id, full_name=name, specialization=spec, education=edu, years=years)
        for name, spec, edu, years in EXPERTS
    ]
    db.add_all(experts)
    db.flush()
    principal = Principal(user=user)
    independence = Independence(
        affiliated=False,
        participated_in_docs=False,
        is_supplier=False,
        joint_experience="Совместной деятельности не было",
    )
    team = [
        MemberIn(expert_id=experts[0].id, role="lead", assigned_items=["3.1", "3.3", "3.4", "3.5"]),
        MemberIn(expert_id=experts[1].id, assigned_items=["3.2"]),
        MemberIn(expert_id=experts[2].id, assigned_items=["3.7"]),
        MemberIn(expert_id=experts[3].id, assigned_items=["4", "5"]),
    ]
    for i, data in enumerate(PROJECTS):
        create_project(
            db,
            principal,
            ProjectCreate(
                **data,
                independence=independence if i != 2 else Independence(),
                members=team if i == 0 else [],
            ),
        )
    db.commit()


if __name__ == "__main__":
    run()
