"""Проверка шаблона ТЗ: JSON-схема (структура) + семантика (ссылки, уникальность, шкалы, зоны)."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from jsonschema import Draft202012Validator

from app.template_engine.expr import ExpressionError, compile_expr

SCHEMA_PATH = Path(__file__).parent / "schema.json"


@dataclass(frozen=True)
class Issue:
    level: Literal["error", "warning"]
    path: str
    message: str

    def as_dict(self) -> dict:
        return {"level": self.level, "path": self.path, "message": self.message}


class TemplateInvalid(ValueError):
    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in issues if i.level == "error"))


@lru_cache
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _path(parts) -> str:
    out = "$"
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out


def validate_text(yaml_text: str) -> tuple[dict | None, list[Issue]]:
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"строка {mark.line + 1}, позиция {mark.column + 1}" if mark else "?"
        return None, [
            Issue("error", "$", f"Ошибка синтаксиса YAML ({where}): {getattr(exc, 'problem', exc)}")
        ]
    if not isinstance(data, dict):
        return None, [Issue("error", "$", "Шаблон должен быть YAML-объектом")]
    return data, validate(data)


def validate(data: dict) -> list[Issue]:
    issues = [
        Issue("error", _path(e.absolute_path), _schema_message(e))
        for e in sorted(Draft202012Validator(schema()).iter_errors(data), key=lambda e: list(e.absolute_path))
    ]
    if issues:  # семантика имеет смысл только для структурно корректного шаблона
        return issues
    return _semantic(data)


def _schema_message(e) -> str:
    if e.validator == "additionalProperties":
        return f"Неизвестное поле: {e.message.split('(')[-1].rstrip(')')}"
    if e.validator == "required":
        return f"Отсутствует обязательное поле {e.message.split(' ')[0]}"
    if e.validator == "enum":
        return f"Недопустимое значение {e.instance!r}; допустимо: {', '.join(map(str, e.validator_value))}"
    if e.validator == "pattern":
        return f"Значение {e.instance!r} не соответствует формату"
    if e.validator == "not":
        return "У раздела не может быть одновременно items и subsections"
    return e.message


@dataclass
class _Node:
    id: str
    path: str
    node: dict
    parent: str | None
    is_item: bool
    optional: bool  # внутри выключенного по умолчанию раздела


def collect_nodes(data: dict) -> list[_Node]:
    nodes: list[_Node] = []

    def visit(node: dict, path: str, parent: str | None, optional: bool) -> None:
        optional = optional or node.get("enabled", True) is False
        children = node.get("subsections") or node.get("items")
        nodes.append(_Node(str(node["id"]), path, node, parent, is_item=not children, optional=optional))
        key = "subsections" if node.get("subsections") else "items"
        for idx, child in enumerate(children or []):
            visit(child, f"{path}.{key}[{idx}]", str(node["id"]), optional)

    for idx, section in enumerate(data["sections"]):
        visit(section, f"$.sections[{idx}]", None, False)
    return nodes


def match(pattern: str, ids) -> list[str]:
    if pattern.endswith(".*"):
        prefix = pattern[:-1]
        return [i for i in ids if i.startswith(prefix)]
    return [i for i in ids if i == pattern]


def _semantic(data: dict) -> list[Issue]:
    issues: list[Issue] = []
    err = lambda path, msg: issues.append(Issue("error", path, msg))  # noqa: E731
    warn = lambda path, msg: issues.append(Issue("warning", path, msg))  # noqa: E731

    nodes = collect_nodes(data)
    by_id: dict[str, _Node] = {}
    for n in nodes:
        if n.id in by_id:
            err(n.path, f"Повторяющийся id «{n.id}» (уже есть в {by_id[n.id].path})")
        by_id.setdefault(n.id, n)
        if n.parent and not n.id.startswith(n.parent + "."):
            err(n.path, f"id «{n.id}» должен начинаться с id родителя «{n.parent}.»")

    all_ids = list(by_id)
    item_ids = [n.id for n in nodes if n.is_item]
    categories = set(data.get("document_categories", {}))
    roles = set(data.get("expert_roles", []))

    deps_graph: dict[str, set[str]] = {}
    for n in nodes:
        node = n.node
        for code in node.get("inputs", []):
            if code not in categories:
                err(f"{n.path}.inputs", f"Категория «{code}» отсутствует в document_categories")
        role = node.get("expert_role")
        if role and roles and role not in roles:
            err(f"{n.path}.expert_role", f"Роль «{role}» отсутствует в expert_roles")
        resolved: set[str] = set()
        for pattern in node.get("depends_on", []):
            targets = [t for t in match(pattern, all_ids) if t != n.id and not t.startswith(n.id + ".")]
            if not targets:
                err(f"{n.path}.depends_on", f"Ссылка «{pattern}» не указывает ни на один пункт")
            elif not n.optional and all(by_id[t].optional for t in targets):
                warn(
                    f"{n.path}.depends_on",
                    f"«{pattern}» указывает только на необязательные (выключенные) пункты",
                )
            resolved.update(t for t in targets if t in item_ids or not by_id[t].is_item)
        deps_graph[n.id] = resolved
        if node.get("kind") in ("conclusion", "remarks") and n.is_item and not node.get("depends_on"):
            warn(n.path, f"Пункт-{'вывод' if node['kind'] == 'conclusion' else 'замечания'} без depends_on")
        hints = node.get("output_hints", {})
        if "min_alternatives" in hints and not hints.get("tables"):
            warn(f"{n.path}.output_hints", "min_alternatives задан без таблицы сравнения в tables")

    cycle = _find_cycle(deps_graph, by_id)
    if cycle:
        err(by_id[cycle[0]].path, f"Циклическая зависимость: {' → '.join(cycle)}")

    for idx, section in enumerate(data["sections"]):
        if section.get("kind") == "risks":
            _check_risks(section, f"$.sections[{idx}]", all_ids, by_id, err, warn)

    ids = [a["id"] for a in data.get("appendices", [])]
    for dup in {i for i in ids if ids.count(i) > 1}:
        err("$.appendices", f"Повторяющийся id приложения «{dup}»")
    return issues


def _expand(target: str, by_id: dict[str, _Node]) -> set[str]:
    """Ссылка на раздел = все его пункты."""
    node = by_id[target]
    if node.is_item:
        return {target}
    return {i for i, n in by_id.items() if n.is_item and i.startswith(target + ".")}


def _find_cycle(graph: dict[str, set[str]], by_id: dict[str, _Node]) -> list[str] | None:
    edges = {k: set().union(*(_expand(t, by_id) for t in v)) if v else set() for k, v in graph.items()}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(edges, WHITE)
    stack: list[str] = []

    def dfs(u: str) -> list[str] | None:
        color[u] = GRAY
        stack.append(u)
        for v in sorted(edges.get(u, ())):
            if color.get(v) == GRAY:
                return stack[stack.index(v) :] + [v]
            if color.get(v) == WHITE and (found := dfs(v)):
                return found
        stack.pop()
        color[u] = BLACK
        return None

    for node in sorted(edges):
        if color[node] == WHITE and (found := dfs(node)):
            return found
    return None


def _check_risks(section: dict, path: str, all_ids, by_id, err, warn) -> None:
    for scale_name in ("probability_scale", "impact_scale"):
        keys = sorted(int(k) for k in section[scale_name])
        if keys != list(range(1, len(keys) + 1)):
            err(f"{path}.{scale_name}", "Градации шкалы должны идти подряд с 1")
    for zone, expr in section["zones"].items():
        try:
            compile_expr(expr)
        except ExpressionError as exc:
            err(f"{path}.zones.{zone}", str(exc))
    codes = [r["code"] for r in section["mandatory_risks"]]
    for dup in {c for c in codes if codes.count(c) > 1}:
        err(f"{path}.mandatory_risks", f"Повторяющийся код риска «{dup}»")
    for idx, risk in enumerate(section["mandatory_risks"]):
        rpath = f"{path}.mandatory_risks[{idx}]"
        if not risk["group"].startswith(str(section["id"]) + "."):
            err(f"{rpath}.group", f"Группа «{risk['group']}» должна быть подпунктом раздела {section['id']}")
        for pattern in risk.get("related", []):
            targets = match(pattern, all_ids)
            if not targets:
                err(f"{rpath}.related", f"Ссылка «{pattern}» не указывает ни на один пункт")
            elif all(by_id[t].optional for t in targets):
                warn(
                    f"{rpath}.related", f"«{pattern}» указывает только на необязательные (выключенные) пункты"
                )


def errors_only(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]
