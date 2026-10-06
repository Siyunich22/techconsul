"""Версии шаблонов ТЗ (TZ §8): загрузка с проверкой, шаблон по умолчанию, дерево пунктов."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import Principal
from app.models import Project, TemplateVersion
from app.services.audit import audit
from app.template_engine.loader import parse_template
from app.template_engine.validate import Issue, errors_only, validate_text


def check(yaml_text: str) -> tuple[dict | None, list[Issue]]:
    return validate_text(yaml_text)


def create(db: Session, principal: Principal, yaml_text: str, make_default: bool) -> TemplateVersion:
    data, issues = check(yaml_text)
    errors = errors_only(issues)
    if data is None or errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"message": "Шаблон не прошёл проверку", "issues": [i.as_dict() for i in errors]},
        )
    if db.scalar(select(TemplateVersion).where(TemplateVersion.code == data["code"])):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Шаблон с кодом «{data['code']}» уже есть — версии неизменяемы, "
            "задайте новый code (например, с суффиксом _v2)",
        )
    tv = TemplateVersion(
        code=data["code"],
        title=data["title"],
        yaml_text=yaml_text,
        is_default=False,
        warnings_json=[i.as_dict() for i in issues],
        uploaded_by=principal.user_id,
    )
    db.add(tv)
    db.flush()
    if make_default:
        set_default(db, principal, tv)
    audit(db, "template.uploaded", org_id=None, user_id=principal.user_id, template_id=tv.id, code=tv.code)
    db.commit()
    return tv


def set_default(db: Session, principal: Principal, tv: TemplateVersion) -> TemplateVersion:
    db.execute(update(TemplateVersion).where(TemplateVersion.id != tv.id).values(is_default=False))
    tv.is_default = True
    audit(db, "template.default_set", org_id=None, user_id=principal.user_id, template_id=tv.id, code=tv.code)
    return tv


def usage_counts(db: Session) -> dict[uuid.UUID, int]:
    rows = db.execute(select(Project.template_version_id, func.count()).group_by(Project.template_version_id))
    return dict(rows.all())


def delete(db: Session, principal: Principal, tv: TemplateVersion) -> None:
    if tv.is_default:
        raise HTTPException(status.HTTP_409_CONFLICT, "Нельзя удалить шаблон по умолчанию")
    if usage_counts(db).get(tv.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Шаблон используется в проектах — версия зафиксирована в них"
        )
    audit(db, "template.deleted", org_id=None, user_id=principal.user_id, template_id=tv.id, code=tv.code)
    db.delete(tv)
    db.commit()


def get(db: Session, template_id: uuid.UUID) -> TemplateVersion:
    tv = db.get(TemplateVersion, template_id)
    if tv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Шаблон не найден")
    return tv


def summary(tv: TemplateVersion) -> dict:
    from app.template_engine.tree import build_tree, count_items

    data = parse_template(tv.yaml_text)
    tree = build_tree(data)
    risks = next((s for s in data["sections"] if s.get("kind") == "risks"), None)
    return {
        "items_total": count_items(tree),
        "items_optional": count_items(tree, only_enabled=False) - count_items(tree),
        "categories": len(data.get("document_categories", {})),
        "mandatory_risks": len(risks["mandatory_risks"]) if risks else 0,
        "language": data.get("language"),
    }
