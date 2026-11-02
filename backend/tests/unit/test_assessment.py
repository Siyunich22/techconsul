import uuid

import pytest
from pydantic import ValidationError

from app.assessment.calc import CalcError, evaluate
from app.assessment.runner import PlanItem, finalize, plan_items
from app.assessment.tools import SourceRegistry
from app.llm.client import LLMError
from app.schemas.assessment import SectionPayload
from app.template_engine.loader import parse_template, template_path
from app.template_engine.tree import TreeNode

TEMPLATE = parse_template(template_path("tech_assessment_bank_v1").read_text(encoding="utf-8"))


def test_calc_is_safe_arithmetic():
    assert evaluate("round(12500e6 / (300*330), 0)") == 126263.0
    assert evaluate("12,5 * 2") == 25.0  # десятичная запятая
    assert evaluate("max(1, 7, 3) + sum([1, 2])") == 10
    for bad in ("__import__('os')", "2**1000", "1/0", "open('x')", "a + 1"):
        with pytest.raises(CalcError):
            evaluate(bad)


def test_plan_items_from_template():
    plan = {i.id: i for i in plan_items(TEMPLATE, [])}
    assert "3.6.1" not in plan and "4" not in plan and "1" not in plan  # выключенный раздел и разделы-целиком
    assert {i for i, p in plan.items() if p.is_info} == {"2.1", "2.2", "3.1.2"}
    assert plan["3.4.12"].depends_on[:2] == ("3.4.1", "3.4.2")
    assert "3.2.1" in plan["3.1.3"].depends_on and not any(
        d.startswith("3.6") for d in plan["3.1.3"].depends_on
    )
    with_36 = {i.id: i for i in plan_items(TEMPLATE, ["3.6"])}
    assert "3.6.1" in with_36 and "3.6.2" in with_36  # необязательный раздел включён флагом проекта


def _payload(**kw) -> dict:
    base = {
        "item_id": "x",
        "disclosure_status": "full",
        "summary": "s",
        "analysis_md": "a",
        "sources": [{"id": "S1"}],
        "confidence": 0.5,
    }
    return base | kw


def test_section_payload_rule_2():
    with pytest.raises(ValidationError, match="источника"):
        SectionPayload.model_validate(_payload(sources=[], analysis_md="без ссылок"))
    SectionPayload.model_validate(
        _payload(sources=[], analysis_md="ссылка в тексте [S3]")
    )  # дополнит finalize
    with pytest.raises(ValidationError, match="gap"):
        SectionPayload.model_validate(_payload(disclosure_status="not_disclosed", sources=[]))
    with pytest.raises(ValidationError, match="gap"):
        SectionPayload.model_validate(
            _payload(
                disclosure_status="not_disclosed",
                gap={
                    "reason": "нет ПСД",
                    "impact_on_conclusions": "",
                    "assumptions": "-",
                    "recommendation": "-",
                },
            )
        )
    ok = SectionPayload.model_validate(_payload(disclosure_status="not_applicable", sources=[]))
    ok.check_citations()
    with pytest.raises(ValueError, match="S2"):
        SectionPayload.model_validate(_payload(analysis_md="факт [S2]")).check_citations()


def _item(hints: dict | None = None) -> PlanItem:
    node = TreeNode(id="3.4.1", title="t", type="item", kind="analysis", output_hints=hints or {})
    return PlanItem("3.4.1", "t", "analysis", "3.4", node, ())


def test_finalize_maps_web_sources_and_drops_unknown():
    reg = SourceRegistry()
    s1 = reg.doc(uuid.uuid4(), "ТЭО.pdf", 14)
    payload = SectionPayload.model_validate(
        _payload(
            analysis_md=f"Мощность [{s1}], аналоги [W1], выдумка [S77].",
            findings=[{"text": "f", "source_ids": [s1, "S77"]}],
            sources=[{"id": "W1", "type": "web", "url": "https://gov.kz/x", "title": "МИИР"}],
        )
    )
    result, warnings = finalize(payload, _item({"tables": ["Сравнение"]}), reg)
    web_sid = next(s.id for s in result.sources if s.type == "web")
    assert result.analysis_md == f"Мощность [{s1}], аналоги [{web_sid}], выдумка."
    assert result.findings[0].source_ids == [s1]
    assert {s.id for s in result.sources} == {s1, web_sid}
    assert next(s for s in result.sources if s.id == s1).title == "«ТЭО.pdf», стр. 14"
    assert any("S77" in w for w in warnings) and any("таблицу" in w for w in warnings)
    assert reg.doc(reg.entries[s1].document_id, "ТЭО.pdf", 14) == s1  # тот же фрагмент — та же метка


def test_finalize_rejects_full_without_real_sources():
    reg = SourceRegistry()
    payload = SectionPayload.model_validate(_payload(analysis_md="Только выдумка [S5].", sources=[]))
    with pytest.raises(LLMError):
        finalize(payload, _item(), reg)


def test_finalize_accepts_only_sources_the_item_received():
    """Метка из общего реестра прогона, которую пункт не получал от инструментов, — выдумка."""
    reg = SourceRegistry()
    own = reg.doc(uuid.uuid4(), "ТЭО.pdf", 1)
    foreign = reg.doc(uuid.uuid4(), "Смета.xlsx", 2)  # выдана другому пункту
    payload = SectionPayload.model_validate(
        _payload(
            analysis_md=f"Факт [{own}], чужое [{foreign}], висячий веб [W9].",
            findings=[{"text": "f", "source_ids": [own]}],
            sources=[{"id": "W2", "type": "web", "url": "javascript:alert(1)", "title": "x"}],
        )
    )
    result, warnings = finalize(payload, _item(), reg, allowed={own})
    assert result.analysis_md == f"Факт [{own}], чужое, висячий веб."
    assert {s.id for s in result.sources} == {own}
    assert any(foreign in w and "W9" in w for w in warnings) and any("W2" in w for w in warnings)


@pytest.mark.parametrize(
    "expr", ["round(1, -10**7)", "(-8)**0.5", "max([1],[2])", "10**40", "abs([1])", "sum([])"]
)
def test_calc_rejects_dangerous_or_non_numeric(expr):
    with pytest.raises(CalcError):
        evaluate(expr)


def test_calc_functions_still_work():
    assert evaluate("round(12.5 * 3, 1)") == 37.5
    assert evaluate("max([4, 5])") == 5 and evaluate("min(3, 1, 2)") == 1 and evaluate("sum([1, 2])") == 3
