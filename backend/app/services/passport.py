"""Паспорт проекта: запуск извлечения, правка и подтверждение экспертом (TZ §4.5 Б)."""

import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.core.config import get_settings
from app.models import Document, DocumentStatus, Passport, PassportStatus, Project, UserRole
from app.schemas.passport import (
    Discrepancy,
    Fact,
    PassportData,
    PassportOut,
    PassportProgress,
    PassportUpdate,
    SourceOut,
)
from app.services.audit import audit


class PassportEnqueuer(Protocol):
    def extract(self, passport_id: uuid.UUID, run_id: uuid.UUID) -> None: ...


class CeleryPassportEnqueuer:
    def extract(self, passport_id: uuid.UUID, run_id: uuid.UUID) -> None:
        from workers.tasks import enqueue_passport

        enqueue_passport(passport_id, run_id)


@lru_cache
def get_passport_enqueuer() -> PassportEnqueuer:
    return CeleryPassportEnqueuer()


def _now() -> datetime:
    return datetime.now(UTC)


def require_reviewer(principal: Principal) -> None:
    """Извлекать, править и подтверждать паспорт могут руководитель и эксперты (наблюдатель — нет)."""
    if principal.role not in (UserRole.admin, UserRole.manager, UserRole.expert):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")


def get_passport(db: Session, project: Project) -> Passport | None:
    return db.scalar(
        select(Passport).where(Passport.project_id == project.id, Passport.org_id == project.org_id)
    )


def to_out(passport: Passport | None) -> PassportOut:
    if passport is None:
        return PassportOut(
            status="empty",
            data=PassportData(),
            discrepancies=[],
            sources={},
            progress=PassportProgress(),
            warnings=[],
        )
    return PassportOut(
        status=passport.status.value,
        data=PassportData.model_validate(passport.data_json or {}),
        discrepancies=[Discrepancy.model_validate(d) for d in passport.discrepancies_json or []],
        sources={k: SourceOut.model_validate(v) for k, v in (passport.sources_json or {}).items()},
        progress=PassportProgress.model_validate(passport.progress_json or {}),
        warnings=list(passport.warnings_json or []),
        error=passport.error,
        extracted_at=passport.extracted_at,
        updated_at=passport.updated_at,
        confirmed_at=passport.confirmed_at,
        confirmed_by=passport.confirmer.full_name if passport.confirmer else None,
    )


def _is_running(passport: Passport) -> bool:
    if passport.status != PassportStatus.extracting or passport.started_at is None:
        return False
    return _now() - passport.started_at < timedelta(minutes=get_settings().passport_stale_after_min)


def start_extraction(
    db: Session, enqueuer: PassportEnqueuer, principal: Principal, project: Project
) -> Passport:
    require_reviewer(principal)
    statuses = list(
        db.scalars(
            select(Document.status).where(
                Document.project_id == project.id,
                Document.org_id == project.org_id,
                Document.deleted_at.is_(None),
            )
        )
    )
    if any(s in (DocumentStatus.uploaded, DocumentStatus.processing) for s in statuses):
        raise HTTPException(status.HTTP_409_CONFLICT, "Дождитесь окончания обработки документов")
    if not any(s in (DocumentStatus.recognized, DocumentStatus.indexed) for s in statuses):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Нет обработанных документов — загрузите документы проекта"
        )

    passport = get_passport(db, project)
    if passport is not None and _is_running(passport):
        raise HTTPException(status.HTTP_409_CONFLICT, "Паспорт уже извлекается")
    if passport is None:
        passport = Passport(org_id=project.org_id, project_id=project.id, status=PassportStatus.extracting)
        db.add(passport)
    run_id = uuid.uuid4()
    passport.status, passport.run_id, passport.error = PassportStatus.extracting, run_id, None
    passport.progress_json, passport.warnings_json = {}, []
    passport.started_at, passport.started_by = _now(), principal.user_id
    db.flush()
    audit(
        db,
        "passport.extract_started",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        run_id=run_id,
    )
    db.commit()
    enqueuer.extract(passport.id, run_id)
    db.refresh(passport)
    return passport


def _editable(db: Session, project: Project) -> Passport:
    passport = get_passport(db, project)
    if passport is None or passport.status in (PassportStatus.extracting, PassportStatus.failed):
        raise HTTPException(status.HTTP_409_CONFLICT, "Паспорт ещё не извлечён")
    return passport


def _check_sources(data: PassportData, discrepancies: list[Discrepancy], sources: dict) -> None:
    """Правило 2: значение без источника допустимо, только если его ввёл эксперт (manual)."""
    errors: list[str] = []
    for key in PassportData.model_fields:
        section = getattr(data, key)
        for fkey in type(section).model_fields:
            value = getattr(section, fkey)
            items = [value] if isinstance(value, Fact) else value if isinstance(value, list) else []
            for n, item in enumerate(items):
                where = f"{key}.{fkey}" + (f"[{n}]" if isinstance(value, list) else "")
                unknown = [r for r in item.refs if r not in sources]
                if unknown:
                    errors.append(f"{where}: неизвестные источники {', '.join(unknown)}")
                elif not item.refs and not item.manual:
                    errors.append(f"{where}: нет источника")
    for n, d in enumerate(discrepancies):
        for v in d.values:
            if any(r not in sources for r in v.refs):
                errors.append(f"Расхождение {n + 1}: неизвестный источник")
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(errors[:10]))


def update_passport(db: Session, principal: Principal, project: Project, body: PassportUpdate) -> Passport:
    require_reviewer(principal)
    passport = _editable(db, project)
    _check_sources(body.data, body.discrepancies, passport.sources_json or {})
    passport.data_json = body.data.model_dump(mode="json")
    passport.discrepancies_json = [d.model_dump(mode="json") for d in body.discrepancies]
    was_confirmed = passport.status == PassportStatus.confirmed
    passport.status, passport.updated_by = PassportStatus.draft, principal.user_id
    passport.confirmed_by = passport.confirmed_at = None
    audit(
        db,
        "passport.updated",
        org_id=project.org_id,
        user_id=principal.user_id,
        project_id=project.id,
        confirmation_reset=was_confirmed,
    )
    db.commit()
    db.refresh(passport)
    return passport


def confirm_passport(db: Session, principal: Principal, project: Project) -> Passport:
    require_reviewer(principal)
    passport = _editable(db, project)
    if passport.status == PassportStatus.confirmed:
        return passport
    _check_sources(
        PassportData.model_validate(passport.data_json),
        [Discrepancy.model_validate(d) for d in passport.discrepancies_json],
        passport.sources_json or {},
    )
    passport.status = PassportStatus.confirmed
    passport.confirmed_by, passport.confirmed_at = principal.user_id, _now()
    audit(db, "passport.confirmed", org_id=project.org_id, user_id=principal.user_id, project_id=project.id)
    db.commit()
    db.refresh(passport)
    return passport
