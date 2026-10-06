"""Загрузка YAML-шаблона ТЗ; проверка — validate.py, дерево пунктов — tree.py."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import TemplateVersion


@dataclass(frozen=True)
class AssignableSection:
    id: str
    title: str
    expert_role: str | None
    optional: bool  # enabled: false — включается флагом проекта
    extension: bool


@lru_cache(maxsize=32)
def parse_template(yaml_text: str) -> dict:
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict) or "code" not in data or "sections" not in data:
        raise ValueError("Шаблон должен содержать поля code и sections")
    return data


def assignable_sections(template: dict) -> list[AssignableSection]:
    """Разделы, за которыми закрепляются эксперты: подразделы (3.1…3.7) и аналитические разделы
    верхнего уровня без подразделов (4, 5). Чисто информационные разделы (kind: info) не закрепляются."""
    result: list[AssignableSection] = []
    for section in template["sections"]:
        subsections = section.get("subsections")
        if subsections:
            result.extend(_to_assignable(sub) for sub in subsections)
        elif "items" not in section and section.get("kind") not in (None, "info"):
            result.append(_to_assignable(section))
    return result


@dataclass(frozen=True)
class TemplateItem:
    id: str
    title: str
    section_id: str
    inputs: tuple[str, ...]
    requires_reference: tuple[str, ...]
    node: dict


def iter_items(template: dict, enabled_optional: list[str] | tuple[str, ...] = ()) -> list[TemplateItem]:
    """Пункты шаблона по порядку; пункты выключенных необязательных разделов пропускаются."""
    result: list[TemplateItem] = []

    def visit(node: dict, section_id: str, enabled: bool) -> None:
        node_id = str(node.get("id", ""))
        if node.get("enabled", True) is False and node_id not in enabled_optional:
            enabled = False
        if not enabled:
            return
        children = node.get("subsections") or node.get("items")
        if children:
            # пункты получают id ближайшего узла с items (подраздел 3.4 или раздел 2)
            for child in children:
                visit(child, node_id if node.get("items") else section_id, enabled)
        elif node_id:
            result.append(
                TemplateItem(
                    id=node_id,
                    title=node.get("title", ""),
                    section_id=section_id,
                    inputs=tuple(node.get("inputs", [])),
                    requires_reference=tuple(node.get("requires_reference", [])),
                    node=node,
                )
            )

    for section in template["sections"]:
        visit(section, str(section["id"]), True)
    return result


def document_categories(template: dict) -> dict[str, str]:
    return dict(template.get("document_categories") or {})


def _to_assignable(node: dict) -> AssignableSection:
    return AssignableSection(
        id=str(node["id"]),
        title=node["title"],
        expert_role=node.get("expert_role"),
        optional=node.get("enabled", True) is False,
        extension=bool(node.get("extension", False)),
    )


def template_path(code: str) -> Path:
    return get_settings().templates_dir / f"{code}.yaml"


def ensure_default_template(db: Session) -> TemplateVersion:
    """Идемпотентно регистрирует шаблон по умолчанию из templates/<code>.yaml."""
    code = get_settings().default_template_code
    existing = db.scalar(select(TemplateVersion).where(TemplateVersion.code == code))
    if existing:
        return existing
    from app.template_engine.validate import TemplateInvalid, errors_only, validate_text

    yaml_text = template_path(code).read_text(encoding="utf-8")
    data, issues = validate_text(yaml_text)
    if data is None or errors_only(issues):
        raise TemplateInvalid(errors_only(issues))
    tv = TemplateVersion(
        code=data["code"],
        title=data["title"],
        yaml_text=yaml_text,
        is_default=True,
        warnings_json=[i.as_dict() for i in issues],
    )
    db.add(tv)
    db.commit()
    return tv
