"""Движок шаблона: схема, семантические проверки, дерево пунктов, выражения зон риска."""

import copy

import pytest
import yaml

from app.template_engine.expr import ExpressionError, compile_expr, evaluate, zone_for
from app.template_engine.loader import template_path
from app.template_engine.tree import build_tree, count_items
from app.template_engine.validate import errors_only, validate, validate_text

YAML_TEXT = template_path("tech_assessment_bank_v1").read_text(encoding="utf-8")
BASE = yaml.safe_load(YAML_TEXT)


def _data():
    return copy.deepcopy(BASE)


def _item(data, item_id):
    for s in data["sections"]:
        for sub in s.get("subsections", []):
            for it in sub["items"]:
                if it["id"] == item_id:
                    return it
        for it in s.get("items", []):
            if it["id"] == item_id:
                return it
    raise KeyError(item_id)


def _risks(data):
    return next(s for s in data["sections"] if s.get("kind") == "risks")


def _errors(data):
    return [(i.path, i.message) for i in errors_only(validate(data))]


def test_attached_template_is_valid():
    """Критерий приёмки фазы 3: приложенный YAML валиден."""
    data, issues = validate_text(YAML_TEXT)
    assert data is not None
    assert errors_only(issues) == []
    # единственные предупреждения — обязательные риски, опирающиеся на выключенный по умолчанию раздел 3.6
    assert issues and all(i.level == "warning" and "необязательные" in i.message for i in issues)
    assert {i.path for i in issues} == {
        f"$.sections[3].mandatory_risks[{n}].related" for n in (9, 11, 12, 13, 14)
    }


def test_tree_is_built_from_template():
    """Критерий приёмки фазы 3: дерево пунктов строится из шаблона."""
    tree = build_tree(BASE)
    assert [n.id for n in tree] == ["1", "2", "3", "4", "5", "6"]
    section3 = tree[2]
    assert [s.id for s in section3.children] == ["3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7"]
    s34 = section3.children[3]
    assert s34.type == "subsection" and len(s34.children) == 13
    it = s34.children[0]
    assert it.id == "3.4.1" and it.type == "item" and it.output_hints["min_alternatives"] == 3
    concl = s34.children[11]
    assert concl.kind == "conclusion" and concl.depends_on[0] == "3.4.1" and len(concl.depends_on) == 11
    s36 = section3.children[5]
    assert s36.optional and not s36.enabled and all(not c.enabled for c in s36.children)
    assert tree[3].kind == "risks" and "3.4.13" in tree[3].depends_on  # маска «3.*» раскрыта в пункты


def test_optional_section_enabled_by_project():
    tree = build_tree(BASE, ["3.6"])
    s36 = tree[2].children[5]
    assert s36.enabled and all(c.enabled for c in s36.children)
    assert count_items(tree) == count_items(build_tree(BASE)) + 2


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d.update(unknown_field=1), "Неизвестное поле"),
        (lambda d: d.pop("sections"), "Отсутствует обязательное поле"),
        (lambda d: _item(d, "3.4.1").update(kind="analysys"), "Недопустимое значение"),
        (lambda d: _item(d, "3.4.1").update(tools=["web", "telepathy"]), "Недопустимое значение"),
        (lambda d: _item(d, "3.4.1").update(id="3.4.x"), "не соответствует формату"),
        (lambda d: _item(d, "3.4.1").pop("requirement"), "Отсутствует обязательное поле"),
        (lambda d: _risks(d).pop("zones"), "zones"),
    ],
)
def test_schema_errors(mutate, expected):
    data = _data()
    mutate(data)
    errors = _errors(data)
    assert errors and any(expected in msg for _, msg in errors), errors


def test_semantic_errors():
    d = _data()
    _item(d, "3.4.2")["id"] = "3.4.1"
    assert any("Повторяющийся id «3.4.1»" in m for _, m in _errors(d))

    d = _data()
    _item(d, "3.4.2")["id"] = "3.5.99"
    assert any("должен начинаться с id родителя «3.4.»" in m for _, m in _errors(d))

    d = _data()
    _item(d, "3.4.1")["inputs"] = ["тэо_bfs", "чертежи"]
    assert any("Категория «чертежи» отсутствует" in m for _, m in _errors(d))

    d = _data()
    _item(d, "3.4.12")["depends_on"] = ["3.9.*"]
    assert any("«3.9.*» не указывает ни на один пункт" in m for _, m in _errors(d))

    d = _data()
    _item(d, "3.4.1")["expert_role"] = "астролог"
    assert any("Роль «астролог»" in m for _, m in _errors(d))

    d = _data()
    _item(d, "3.4.1")["depends_on"] = ["3.4.13"]  # 3.4.13 зависит от 3.4.1 → цикл
    assert any("Циклическая зависимость" in m for _, m in _errors(d))


def test_risk_section_checks():
    d = _data()
    _risks(d)["probability_scale"] = {"1": "Низкая", "3": "Высокая"}
    assert any("подряд с 1" in m for _, m in _errors(d))

    d = _data()
    _risks(d)["zones"]["red"] = "__import__('os').system('rm -rf /')"
    assert any("Недопустимая" in m for _, m in _errors(d))

    d = _data()
    _risks(d)["mandatory_risks"][1]["code"] = "T1"
    assert any("Повторяющийся код риска «T1»" in m for _, m in _errors(d))

    d = _data()
    _risks(d)["mandatory_risks"][0]["group"] = "5.1"
    assert any("должна быть подпунктом раздела 4" in m for _, m in _errors(d))


def test_yaml_syntax_error_is_reported_with_position():
    data, issues = validate_text("code: x\nsections: [\n  - id: 1\n")
    assert data is None and "Ошибка синтаксиса YAML" in issues[0].message and "строка" in issues[0].message


def test_zone_expressions():
    zones = _risks(BASE)["zones"]
    assert zone_for(zones, 1, 1) == "green"
    assert zone_for(zones, 2, 3) == "yellow"  # 6 ≥ 5
    assert zone_for(zones, 4, 3) == "red"  # p ≥ 4 и i = 3
    assert zone_for(zones, 5, 2) == "red"  # 10 ≥ 10
    assert zone_for(zones, 4, 1) == "green"  # 4 < 5
    assert evaluate("1 < p <= 3", 2, 1) and not evaluate("not (p > 1)", 2, 1)


@pytest.mark.parametrize("bad", ["x > 1", "p.__class__", "open('f')", "p if i else 1", "'a' == 'a'", "p >"])
def test_expression_safety(bad):
    with pytest.raises(ExpressionError):
        compile_expr(bad)
