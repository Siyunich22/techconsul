"""Изоляция организаций: все выборки tenant-сущностей идут через эти функции (CLAUDE.md, правило 4)."""

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session


def scoped[T](model: type[T], org_id: uuid.UUID) -> Select[tuple[T]]:
    """SELECT по модели с обязательным фильтром org_id."""
    return select(model).where(model.org_id == org_id)  # type: ignore[attr-defined]


def get_scoped[T](db: Session, model: type[T], obj_id: uuid.UUID, org_id: uuid.UUID, *opts: Any) -> T:
    """Объект своей организации или 404 (не 403 — не раскрываем существование чужих объектов)."""
    stmt = scoped(model, org_id).where(model.id == obj_id)  # type: ignore[attr-defined]
    if opts:
        stmt = stmt.options(*opts)
    obj = db.scalar(stmt)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")
    return obj
