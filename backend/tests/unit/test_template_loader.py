from app.template_engine.loader import assignable_sections, parse_template, template_path


def test_default_template_assignable_sections():
    tpl = parse_template(template_path("tech_assessment_bank_v1").read_text(encoding="utf-8"))
    sections = assignable_sections(tpl)
    ids = [s.id for s in sections]
    assert ids == ["3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7", "4", "5"]
    by_id = {s.id: s for s in sections}
    assert by_id["3.6"].optional and by_id["3.6"].extension
    assert not by_id["3.4"].optional
    assert by_id["3.7"].expert_role == "эколог"


def test_info_sections_are_not_assignable():
    tpl = {
        "code": "t",
        "sections": [
            {"id": "1", "title": "Структура", "kind": "info"},
            {"id": "2", "title": "Общие", "items": [{"id": "2.1", "title": "x", "kind": "info"}]},
            {"id": "9", "title": "Риски", "kind": "risks"},
        ],
    }
    assert [s.id for s in assignable_sections(tpl)] == ["9"]
