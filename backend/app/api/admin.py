"""Администрирование платформы: справочная библиотека (TZ §2, §13 — справочники НДТ входят в фазу 2)."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession, Principal
from app.core.config import get_settings
from app.core.storage import Storage, get_storage, make_key
from app.models import ReferenceDoc, ReferenceKind, UserRole
from app.pipeline.ingest import is_supported, mime_for
from app.pipeline.references import search_references
from app.schemas.document import ReferenceDocOut
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
