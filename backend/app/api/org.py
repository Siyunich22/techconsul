import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import exists, select

from app.api.deps import CurrentPrincipal, DbSession, ManagerPrincipal
from app.core.config import get_settings
from app.core.mailer import Mailer, get_mailer
from app.core.storage import Storage, get_storage, make_key
from app.core.uploads import CV_TYPES, DOCX_TYPES, LOGO_TYPES, validate_upload
from app.models import Expert, Organization, ProjectMember, User
from app.schemas.org import (
    ExpertIn,
    ExpertOut,
    ExpertPatch,
    InviteIn,
    InviteOut,
    OrgOut,
    OrgPatch,
    OrgUserOut,
)
from app.services import auth as auth_svc
from app.services.audit import audit
from app.services.tenancy import get_scoped, scoped

router = APIRouter(prefix="/org", tags=["org"])

StorageDep = Annotated[Storage, Depends(get_storage)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]
FileUpload = Annotated[UploadFile, File()]


def _org(db, principal) -> Organization:
    org = db.get(Organization, principal.org_id)
    assert org is not None
    return org


def _download(storage: Storage, key: str, filename: str) -> StreamingResponse:
    obj = storage.get(key)
    return StreamingResponse(
        obj.body,
        media_type=obj.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# --- организация ---


@router.get("", response_model=OrgOut)
def get_org(principal: CurrentPrincipal, db: DbSession):
    return _org(db, principal)


@router.patch("", response_model=OrgOut)
def patch_org(data: OrgPatch, principal: ManagerPrincipal, db: DbSession):
    org = _org(db, principal)
    fields = data.model_dump(exclude_unset=True, exclude_none=True, exclude={"settings"})
    for field, value in fields.items():
        setattr(org, field, value)
    if data.settings is not None:
        org.settings_json = data.settings.model_dump()
    audit(db, "org.updated", org_id=org.id, user_id=principal.user_id, fields=sorted(data.model_fields_set))
    db.commit()
    return org


@router.post("/logo", response_model=OrgOut)
def upload_logo(principal: ManagerPrincipal, db: DbSession, storage: StorageDep, file: FileUpload):
    name, ctype = validate_upload(file, LOGO_TYPES, get_settings().max_logo_size_mb)
    org = _org(db, principal)
    key = make_key("orgs", str(org.id), "logo", filename=name)
    storage.put(key, file.file, ctype)
    org.logo_key = key
    audit(db, "org.logo_uploaded", org_id=org.id, user_id=principal.user_id, key=key)
    db.commit()
    return org


@router.get("/logo")
def get_logo(principal: CurrentPrincipal, db: DbSession, storage: StorageDep):
    org = _org(db, principal)
    if not org.logo_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Логотип не загружен")
    obj = storage.get(org.logo_key)
    return StreamingResponse(obj.body, media_type=obj.content_type)


@router.post("/reference-docx", response_model=OrgOut)
def upload_reference_docx(principal: ManagerPrincipal, db: DbSession, storage: StorageDep, file: FileUpload):
    name, ctype = validate_upload(file, DOCX_TYPES, get_settings().max_reference_docx_size_mb)
    org = _org(db, principal)
    key = make_key("orgs", str(org.id), "reference_docx", filename=name)
    storage.put(key, file.file, ctype)
    org.reference_docx_key = key
    audit(db, "org.reference_docx_uploaded", org_id=org.id, user_id=principal.user_id, key=key)
    db.commit()
    return org


@router.get("/reference-docx")
def get_reference_docx(principal: CurrentPrincipal, db: DbSession, storage: StorageDep):
    org = _org(db, principal)
    if not org.reference_docx_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reference.docx не загружен")
    return _download(storage, org.reference_docx_key, "reference.docx")


# --- пользователи и приглашения ---


@router.get("/users", response_model=list[OrgUserOut])
def list_users(principal: ManagerPrincipal, db: DbSession):
    return db.scalars(scoped(User, principal.org_id).order_by(User.full_name)).all()


@router.post("/invitations", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
def invite(data: InviteIn, principal: ManagerPrincipal, db: DbSession, mailer: MailerDep):
    expert = get_scoped(db, Expert, data.expert_id, principal.org_id) if data.expert_id else None
    user, link = auth_svc.invite_user(
        db,
        mailer,
        org=_org(db, principal),
        inviter=principal.user,
        email=data.email,
        full_name=data.full_name,
        role=data.role,
    )
    if expert is not None:
        expert.user_id = user.id
        if not expert.email:
            expert.email = user.email
        db.commit()
    return InviteOut(user=OrgUserOut.model_validate(user), invite_link=link)


# --- реестр экспертов ---


@router.get("/experts", response_model=list[ExpertOut])
def list_experts(principal: CurrentPrincipal, db: DbSession, include_inactive: bool = False):
    stmt = scoped(Expert, principal.org_id).order_by(Expert.full_name)
    if not include_inactive:
        stmt = stmt.where(Expert.is_active.is_(True))
    return db.scalars(stmt).all()


@router.post("/experts", response_model=ExpertOut, status_code=status.HTTP_201_CREATED)
def create_expert(data: ExpertIn, principal: ManagerPrincipal, db: DbSession):
    expert = Expert(org_id=principal.org_id, **data.model_dump())
    db.add(expert)
    db.flush()
    audit(db, "expert.created", org_id=principal.org_id, user_id=principal.user_id, expert_id=expert.id)
    db.commit()
    return expert


@router.get("/experts/{expert_id}", response_model=ExpertOut)
def get_expert(expert_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession):
    return get_scoped(db, Expert, expert_id, principal.org_id)


@router.patch("/experts/{expert_id}", response_model=ExpertOut)
def patch_expert(expert_id: uuid.UUID, data: ExpertPatch, principal: ManagerPrincipal, db: DbSession):
    expert = get_scoped(db, Expert, expert_id, principal.org_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is None and field in ("full_name", "specialization", "is_active"):
            continue
        setattr(expert, field, value)
    audit(db, "expert.updated", org_id=principal.org_id, user_id=principal.user_id, expert_id=expert.id)
    db.commit()
    return expert


@router.delete("/experts/{expert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expert(expert_id: uuid.UUID, principal: ManagerPrincipal, db: DbSession):
    expert = get_scoped(db, Expert, expert_id, principal.org_id)
    if db.scalar(select(exists().where(ProjectMember.expert_id == expert.id))):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Эксперт включён в команду проекта — исключите его из команды или сделайте неактивным",
        )
    audit(
        db,
        "expert.deleted",
        org_id=principal.org_id,
        user_id=principal.user_id,
        expert_id=expert.id,
        full_name=expert.full_name,
    )
    db.delete(expert)
    db.commit()


@router.post("/experts/{expert_id}/cv", response_model=ExpertOut)
def upload_cv(
    expert_id: uuid.UUID,
    principal: ManagerPrincipal,
    db: DbSession,
    storage: StorageDep,
    file: FileUpload,
):
    expert = get_scoped(db, Expert, expert_id, principal.org_id)
    name, ctype = validate_upload(file, CV_TYPES, get_settings().max_cv_size_mb)
    key = make_key("orgs", str(principal.org_id), "experts", str(expert.id), "cv", filename=name)
    storage.put(key, file.file, ctype)
    expert.cv_file_key, expert.cv_filename = key, name
    audit(
        db,
        "expert.cv_uploaded",
        org_id=principal.org_id,
        user_id=principal.user_id,
        expert_id=expert.id,
        key=key,
    )
    db.commit()
    return expert


@router.get("/experts/{expert_id}/cv")
def download_cv(expert_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession, storage: StorageDep):
    expert = get_scoped(db, Expert, expert_id, principal.org_id)
    if not expert.cv_file_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Резюме не загружено")
    return _download(storage, expert.cv_file_key, expert.cv_filename or "cv")
