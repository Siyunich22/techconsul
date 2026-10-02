import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(
    db: Session,
    action: str,
    *,
    org_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
    **payload: object,
) -> None:
    """Запись в журнал аудита (TZ §9). Коммит — вместе с основной операцией."""
    db.add(
        AuditLog(
            org_id=org_id,
            user_id=user_id,
            project_id=project_id,
            action=action,
            payload_json={k: _jsonable(v) for k, v in payload.items()},
        )
    )


def _jsonable(v: object) -> object:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    return v
