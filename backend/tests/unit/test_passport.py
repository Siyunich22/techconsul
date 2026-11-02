import uuid

from app.pipeline.passport import (
    SourcePage,
    clean_discrepancies,
    clean_passport,
    is_empty,
    make_batches,
    merge_partials,
)
from app.schemas.passport import Discrepancy, PassportData


def _page(n: int, text: str = "текст", sheet: str | None = None) -> SourcePage:
    return SourcePage(f"P{n}", uuid.uuid4(), f"doc{n}.pdf", "тэо_bfs", n, sheet, text)


def test_batches_respect_limit_and_keep_labels():
    pages = [_page(i, "x" * 40) for i in range(1, 6)]
    batches = make_batches(pages, limit=120)
    assert len(batches) == 5  # каждая страница с подписью > 60 символов — по одной в пакете
    assert batches[0].startswith("[P1] «doc1.pdf», стр. 1")
    assert make_batches(pages, limit=10_000) == ["\n".join(b for b in batches)]
    assert "лист «CAPEX»" in make_batches([_page(1, sheet="CAPEX")], 1000)[0]


def test_clean_passport_drops_items_without_known_source():
    data = PassportData.model_validate(
        {
            "site": {
                "address": {"value": "Костанай", "refs": ["[P1]", "P1", "P9"], "quote": "к" * 500},
                "railway": {"value": "есть", "refs": ["P9"]},
            },
            "equipment": {
                "items": [
                    {"name": "Вальцовый станок", "refs": ["P2"], "manual": True},
                    {"name": "Без источника", "refs": []},
                ]
            },
        }
    )
    clean = clean_passport(data, {"P1", "P2"})
    assert clean.site.address.refs == ["P1"] and len(clean.site.address.quote) == 200
    assert clean.site.railway is None
    assert [r.name for r in clean.equipment.items] == ["Вальцовый станок"]
    assert clean.equipment.items[0].manual is False  # ответ модели не может пометить поле как экспертное


def test_discrepancy_needs_two_sourced_values():
    items = [
        Discrepancy(
            field="a",
            title="t",
            description="d",
            values=[{"value": "1", "refs": ["P1"]}, {"value": "2", "refs": ["P2"]}],
        ),
        Discrepancy(
            field="b",
            title="t",
            description="d",
            values=[{"value": "1", "refs": ["P1"]}, {"value": "2", "refs": ["P7"]}],
        ),
    ]
    assert [d.field for d in clean_discrepancies(items, {"P1", "P2"})] == ["a"]


def test_merge_partials_fallback():
    a = PassportData.model_validate(
        {
            "technology": {"capacity": {"value": "300", "refs": ["P1"], "confidence": 0.6}},
            "equipment": {"items": [{"name": "Сепаратор", "refs": ["P1"]}]},
        }
    )
    b = PassportData.model_validate(
        {
            "technology": {"capacity": {"value": "320", "refs": ["P2"], "confidence": 0.9}},
            "equipment": {
                "items": [{"name": "Сепаратор", "refs": ["P2"]}, {"name": "Энтолейтор", "refs": ["P2"]}]
            },
        }
    )
    merged = merge_partials([a, b])
    assert merged.technology.capacity.value == "320"
    assert [r.name for r in merged.equipment.items] == ["Сепаратор", "Энтолейтор"]
    assert is_empty(PassportData()) and not is_empty(merged)


def test_reextraction_keeps_expert_edits():
    """Правило 3: ручные поля и строки эксперта переживают повторное извлечение, метки — пересчитаны."""
    from app.pipeline.passport import ExtractionResult, keep_expert_edits

    doc = str(uuid.uuid4())
    teo_p3 = {"document_id": doc, "filename": "ТЭО.pdf", "category": "тэо_bfs", "page": 3, "sheet": None}
    teo_p9 = {**teo_p3, "page": 9}
    old = PassportData.model_validate(
        {
            "technology": {"capacity": {"value": "300", "unit": "т/сут", "refs": ["P2"], "manual": True}},
            "equipment": {"items": [{"name": "Сепаратор", "refs": ["P5"], "manual": True}]},
        }
    ).model_dump(mode="json")
    old_sources = {"P2": teo_p3, "P5": teo_p9}
    old_disc = [{"field": "technology.capacity", "title": "t", "description": "d", "resolution": "Верно 300"}]
    new = ExtractionResult(
        data=PassportData.model_validate(
            {
                "technology": {"capacity": {"value": "320", "unit": "т/сут", "refs": ["P1"]}},
                "equipment": {"items": [{"name": "Мельница", "refs": ["P1"]}]},
            }
        ),
        discrepancies=[Discrepancy(field="technology.capacity", title="t", description="d")],
        sources={"P1": teo_p3},
    )
    merged, kept = keep_expert_edits(new, old, old_sources, old_disc)
    assert kept == 2
    cap = merged.data.technology.capacity
    assert cap.value == "300" and cap.manual and cap.refs == ["P1"]  # та же страница — новая метка P1
    names = [r.name for r in merged.data.equipment.items]
    assert names == ["Мельница", "Сепаратор"]
    added = merged.data.equipment.items[1].refs[0]
    assert merged.sources[added]["page"] == 9  # источника не было в новом извлечении — добавлен
    assert merged.discrepancies[0].resolution == "Верно 300"
    assert any("правки эксперта" in w for w in merged.warnings)
