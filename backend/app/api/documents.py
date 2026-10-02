import tempfile
import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DbSession, ManagerPrincipal
from app.core.storage import Storage, get_storage
from app.models import DocumentPage
from app.pipeline.index import search_project
from app.pipeline.ingest.base import IngestError
from app.schemas.document import (
    CategoryPatch,
    CompletenessOut,
    DocumentOut,
    PageOut,
    SearchHitOut,
    UploadInit,
    UploadInitOut,
)
from app.services import documents as svc
from app.services.projects import get_project

router = APIRouter(prefix="/projects/{project_id}", tags=["documents"])

StorageDep = Annotated[Storage, Depends(get_storage)]
EnqueuerDep = Annotated[svc.Enqueuer, Depends(svc.get_enqueuer)]


# --- загрузка частями (S3 multipart) ---


@router.post("/uploads", response_model=UploadInitOut, status_code=status.HTTP_201_CREATED)
def init_upload(
    project_id: uuid.UUID, data: UploadInit, principal: CurrentPrincipal, db: DbSession, storage: StorageDep
):
    project = get_project(db, principal, project_id)
    upload = svc.init_upload(db, storage, principal, project, data)
    return UploadInitOut(
        upload_id=upload.id, chunk_size=upload.chunk_size, parts_total=svc.parts_total(upload)
    )


@router.put("/uploads/{upload_id}/parts/{part_no}", status_code=status.HTTP_204_NO_CONTENT)
async def put_part(
    project_id: uuid.UUID,
    upload_id: uuid.UUID,
    part_no: int,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    storage: StorageDep,
):
    project = get_project(db, principal, project_id)
    upload = svc.get_upload(db, project, upload_id)
    data = await request.body()
    svc.put_part(db, storage, upload, part_no, data)


@router.post("/uploads/{upload_id}/complete", response_model=DocumentOut)
def complete_upload(
    project_id: uuid.UUID,
    upload_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    storage: StorageDep,
    enqueuer: EnqueuerDep,
):
    project = get_project(db, principal, project_id)
    return svc.complete_upload(
        db, storage, enqueuer, principal, project, svc.get_upload(db, project, upload_id)
    )


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def abort_upload(
    project_id: uuid.UUID,
    upload_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    storage: StorageDep,
):
    project = get_project(db, principal, project_id)
    svc.abort_upload(db, storage, svc.get_upload(db, project, upload_id))


# --- документы ---


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return svc.list_documents(db, get_project(db, principal, project_id))


@router.get("/documents/{doc_id}", response_model=DocumentOut)
def get_document(project_id: uuid.UUID, doc_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return svc.get_document(db, get_project(db, principal, project_id), doc_id)


@router.patch("/documents/{doc_id}", response_model=DocumentOut)
def patch_document(
    project_id: uuid.UUID, doc_id: uuid.UUID, data: CategoryPatch, principal: CurrentPrincipal, db: DbSession
):
    project = get_project(db, principal, project_id)
    return svc.set_category(db, principal, project, svc.get_document(db, project, doc_id), data.category)


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(project_id: uuid.UUID, doc_id: uuid.UUID, principal: ManagerPrincipal, db: DbSession):
    project = get_project(db, principal, project_id)
    svc.delete_document(db, principal, project, svc.get_document(db, project, doc_id))


@router.post("/documents/{doc_id}/reprocess", response_model=DocumentOut)
def reprocess(
    project_id: uuid.UUID,
    doc_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    enqueuer: EnqueuerDep,
):
    project = get_project(db, principal, project_id)
    return svc.reprocess(db, enqueuer, principal, project, svc.get_document(db, project, doc_id))


@router.get("/documents/{doc_id}/file")
def download_file(
    project_id: uuid.UUID, doc_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, storage: StorageDep
):
    doc = svc.get_document(db, get_project(db, principal, project_id), doc_id)
    obj = storage.get(doc.file_key)
    return StreamingResponse(
        obj.body,
        media_type=doc.mime,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.filename)}"},
    )


@router.get("/documents/{doc_id}/pages/{page_no}", response_model=PageOut)
def get_page(
    project_id: uuid.UUID, doc_id: uuid.UUID, page_no: int, principal: CurrentPrincipal, db: DbSession
):
    doc = svc.get_document(db, get_project(db, principal, project_id), doc_id)
    page = db.scalar(
        select(DocumentPage).where(DocumentPage.document_id == doc.id, DocumentPage.page_no == page_no)
    )
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Страница не найдена")
    return PageOut(
        page_no=page.page_no,
        pages_total=doc.pages or 0,
        sheet=page.sheet,
        text=page.text,
        tables=page.tables_json,
        ocr=page.ocr,
        has_image=_image_source(doc) is not None,
    )


def _image_source(doc) -> tuple[str, str] | None:
    """(ключ в хранилище, тип рендера): PDF-оригинал или PDF-превью офисного файла,
    изображение или PNG-превью чертежа."""
    if doc.kind == "pdf":
        return doc.file_key, "pdf"
    if doc.kind == "office" and doc.preview_key:
        return doc.preview_key, "pdf"
    if doc.kind == "image":
        return doc.file_key, "image"
    if doc.kind == "cad" and doc.preview_key:
        return doc.preview_key, "image"
    return None


@router.get("/documents/{doc_id}/pages/{page_no}/image")
def page_image(
    project_id: uuid.UUID,
    doc_id: uuid.UUID,
    page_no: int,
    principal: CurrentPrincipal,
    db: DbSession,
    storage: StorageDep,
    q: Annotated[str | None, Query(max_length=500)] = None,
):
    """Рендер страницы с подсветкой фрагмента q (переход по ссылке-источнику, TZ §4.5 А)."""
    doc = svc.get_document(db, get_project(db, principal, project_id), doc_id)
    source = _image_source(doc)
    if source is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Для этого формата доступен только текстовый предпросмотр"
        )
    key, mode = source
    if mode == "image":
        obj = storage.get(key)
        return StreamingResponse(obj.body, media_type=obj.content_type)
    from app.pipeline.ingest.pdf import page_png

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "doc.pdf"
        storage.download(key, path)
        try:
            png = page_png(path, page_no, highlight=q)
        except IngestError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/completeness", response_model=CompletenessOut)
def completeness(project_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return svc.completeness(db, get_project(db, principal, project_id))


@router.get("/search", response_model=list[SearchHitOut])
def search(
    project_id: uuid.UUID,
    principal: CurrentPrincipal,
    db: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=500)],
    k: Annotated[int, Query(ge=1, le=50)] = 10,
    category: Annotated[list[str] | None, Query()] = None,
):
    project = get_project(db, principal, project_id)
    hits = search_project(db, org_id=project.org_id, project_id=project.id, query=q, k=k, categories=category)
    return [SearchHitOut(**h.__dict__) for h in hits]
