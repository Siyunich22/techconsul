"""Администрирование платформы: справочная библиотека (TZ §13), шаблоны ТЗ (TZ §8)."""

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession, Principal
from app.core.config import get_settings
from app.core.storage import Storage, get_storage, make_key
from app.models import ReferenceDoc, ReferenceKind, TemplateVersion, UserRole
from app.pipeline.ingest import is_supported, mime_for
from app.pipeline.references import search_references
from app.schemas.document import ReferenceDocOut
from app.services import templates as templates_svc
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin"])

StorageDep = Annotated[Storage, Depends(get_storage)]


def require_admin(principal: CurrentPrincipal) -> Principal:
    if principal.role != UserRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Требуются права администратора платформы")
    return principal


AdminPrincipal = Annotated[Principal, Depends(require_admin)]


class ReferenceEnqueuer:
    def ingest(self, ref_id: uuid.UUID) -> None:
        from workers.tasks import ingest_reference_task

        ingest_reference_task.delay(str(ref_id))


def get_reference_enqueuer() -> ReferenceEnqueuer:
    return ReferenceEnqueuer()


@router.get("/reference-docs", response_model=list[ReferenceDocOut])
def list_reference_docs(_: AdminPrincipal, db: DbSession):
    return db.scalars(select(ReferenceDoc).order_by(ReferenceDoc.kind, ReferenceDoc.title)).all()


@router.post("/reference-docs", response_model=ReferenceDocOut, status_code=status.HTTP_201_CREATED)
def upload_reference_doc(
    principal: AdminPrincipal,
    db: DbSession,
    storage: StorageDep,
    enqueuer: Annotated[ReferenceEnqueuer, Depends(get_reference_enqueuer)],
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1)],
    kind: Annotated[ReferenceKind, Form()],
    valid_from: Annotated[date | None, Form()] = None,
    valid_to: Annotated[date | None, Form()] = None,
):
    name = file.filename or "document"
    if not is_supported(name) or name.lower().endswith((".zip", ".rar", ".7z")):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Формат не поддерживается")
    limit = get_settings().max_document_size_mb * 1024**2
    if (file.size or 0) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Файл слишком большой")
    key = make_key("reference", kind.value, filename=name)
    storage.put(key, file.file, mime_for(name))
    ref = ReferenceDoc(
        title=title,
        kind=kind,
        filename=name,
        file_key=key,
        size=file.size or 0,
        valid_from=valid_from,
        valid_to=valid_to,
        uploaded_by=principal.user_id,
    )
    db.add(ref)
    db.flush()
    audit(db, "reference.uploaded", org_id=None, user_id=principal.user_id, reference_id=ref.id, title=title)
    db.commit()
    enqueuer.ingest(ref.id)
    return ref


@router.delete("/reference-docs/{ref_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reference_doc(ref_id: uuid.UUID, principal: AdminPrincipal, db: DbSession):
    ref = db.get(ReferenceDoc, ref_id)
    if ref is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")
    audit(
        db, "reference.deleted", org_id=None, user_id=principal.user_id, reference_id=ref.id, title=ref.title
    )
    db.delete(ref)  # чанки — каскадом; файл в хранилище остаётся
    db.commit()


@router.get("/reference-docs/search")
def search_reference_docs(
    _: AdminPrincipal, db: DbSession, q: str, k: int = 8, kind: list[str] | None = None
):
    return search_references(db, q, k=k, kinds=kind)


# --- шаблоны ТЗ (TZ §8) ---


class TemplateAdminOut(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    is_default: bool
    created_at: datetime
    projects: int
    warnings: list[dict]
    summary: dict


def _template_out(tv: TemplateVersion, usage: dict) -> TemplateAdminOut:
    return TemplateAdminOut(
        id=tv.id,
        code=tv.code,
        title=tv.title,
        is_default=tv.is_default,
        created_at=tv.created_at,
        projects=usage.get(tv.id, 0),
        warnings=tv.warnings_json or [],
        summary=templates_svc.summary(tv),
    )


async def _read_yaml(file: UploadFile | None, yaml_text: str | None) -> str:
    if file is not None:
        raw = await file.read()
        if len(raw) > 2 * 1024**2:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Шаблон больше 2 МБ")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Шаблон должен быть в кодировке UTF-8"
            ) from exc
    if yaml_text:
        return yaml_text
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Передайте файл YAML или текст шаблона")


@router.get("/templates", response_model=list[TemplateAdminOut])
def list_templates(_: AdminPrincipal, db: DbSession):
    usage = templates_svc.usage_counts(db)
    rows = db.scalars(
        select(TemplateVersion).order_by(TemplateVersion.is_default.desc(), TemplateVersion.created_at)
    )
    return [_template_out(tv, usage) for tv in rows]


@router.post("/templates/validate")
async def validate_template(
    _: AdminPrincipal,
    file: Annotated[UploadFile | None, File()] = None,
    yaml_text: Annotated[str | None, Form()] = None,
):
    """Проверка без сохранения: ошибки блокируют загрузку, предупреждения — нет."""
    data, issues = templates_svc.check(await _read_yaml(file, yaml_text))
    return {
        "valid": data is not None and not any(i.level == "error" for i in issues),
        "code": data.get("code") if data else None,
        "title": data.get("title") if data else None,
        "issues": [i.as_dict() for i in issues],
    }


@router.post("/templates", response_model=TemplateAdminOut, status_code=status.HTTP_201_CREATED)
async def upload_template(
    principal: AdminPrincipal,
    db: DbSession,
    file: Annotated[UploadFile | None, File()] = None,
    yaml_text: Annotated[str | None, Form()] = None,
    make_default: Annotated[bool, Form()] = False,
):
    tv = templates_svc.create(db, principal, await _read_yaml(file, yaml_text), make_default)
    return _template_out(tv, templates_svc.usage_counts(db))


@router.post("/templates/{template_id}/default", response_model=TemplateAdminOut)
def make_default_template(template_id: uuid.UUID, principal: AdminPrincipal, db: DbSession):
    tv = templates_svc.set_default(db, principal, templates_svc.get(db, template_id))
    db.commit()
    return _template_out(tv, templates_svc.usage_counts(db))


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: uuid.UUID, principal: AdminPrincipal, db: DbSession):
    templates_svc.delete(db, principal, templates_svc.get(db, template_id))
