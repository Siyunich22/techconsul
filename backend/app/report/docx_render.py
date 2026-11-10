"""DOCX отчёта по ГОСТ 7.32-2017 (структура и оформление отчёта) и ГОСТ Р 7.0.97-2016 (поля, шрифт).

- A4, поля: левое 30 мм, правое 15 мм, верхнее и нижнее 20 мм; шрифт и кегль — из настроек организации
  (по умолчанию Times New Roman 12), межстрочный 1,5, абзацный отступ 1,25 см, выравнивание по ширине.
- Титульный лист без номера (входит в нумерацию), номера страниц — внизу по центру арабскими цифрами.
- Структурные элементы (СПИСОК ИСПОЛНИТЕЛЕЙ, СОДЕРЖАНИЕ, ПРИЛОЖЕНИЕ) — прописными по центру; разделы —
  с новой страницы, заголовки с абзацного отступа полужирным без точки в конце.
- Таблицы: «Таблица N – Название» над таблицей слева; ссылки на источники — [n] по приложению А.
- Структура разделов и пунктов — из YAML шаблона; при reference.docx организации — её стили.
"""

import io
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from string import Template
from xml.sax.saxutils import escape

import yaml
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Mm, Pt, RGBColor

from app.report.data import ItemResult, ReportData, SourceInfo, walk_items
from app.schemas.assessment import ResultTable, SectionPayload
from app.template_engine.tree import TreeNode

TEXTS_PATH = Path(__file__).with_name("texts.yaml")
PAGE_TEXT_WIDTH = Mm(210 - 30 - 15)


@lru_cache
def texts() -> dict:
    return yaml.safe_load(TEXTS_PATH.read_text(encoding="utf-8"))


def _t(key: str, **kw) -> str:
    value: object = texts()
    for part in key.split("."):
        value = value[part]  # type: ignore[index]
    return Template(str(value)).safe_substitute({k: str(v) for k, v in kw.items()})


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def xml_safe(value: str) -> str:
    """Управляющие символы (из Word, извлечения PDF, ответа модели) недопустимы в XML документа."""
    return _CONTROL.sub(" ", value)


# --- ссылки на источники: [S12] → [n] в порядке первого упоминания ---


class Citations:
    _REF = re.compile(r"\[(S\d+)\]")

    def __init__(self, sources: dict[str, SourceInfo]) -> None:
        self.sources = sources
        self.order: list[str] = []

    def number(self, sid: str) -> int | None:
        if sid not in self.sources:
            return None
        if sid not in self.order:
            self.order.append(sid)
        return self.order.index(sid) + 1

    def text(self, value: str) -> str:
        def repl(m: re.Match) -> str:
            n = self.number(m.group(1))
            return f"[{n}]" if n else ""

        out = self._REF.sub(repl, xml_safe(value or ""))
        out = re.sub(r"\](\s*)\[", "], [", out)  # [1] [2] → [1], [2]
        return re.sub(r"\s+([.,;:])", r"\1", out).strip()

    def ids(self, sids: list[str]) -> str:
        nums = [n for n in (self.number(s) for s in sids) if n]
        return f" [{', '.join(map(str, nums))}]" if nums else ""


# --- оформление ---


def _set_font(style_or_run, name: str, size: Pt | None = None, bold: bool | None = None) -> None:
    font = style_or_run.font
    font.name = name
    if size is not None:
        font.size = size
    if bold is not None:
        font.bold = bold
    font.color.rgb = RGBColor(0, 0, 0)
    rpr = style_or_run.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), name)
    # шрифт темы (asciiTheme…) важнее явного — у заголовков и подписей шаблона python-docx он задан
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        fonts.attrib.pop(qn(attr), None)


def _ensure_style(doc, name: str, base: str = "Normal"):
    styles = doc.styles
    try:
        return styles[name]
    except KeyError:
        style = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = styles[base]
        return style


def _apply_gost_styles(doc, font: str, size: int) -> None:
    normal = doc.styles["Normal"]
    _set_font(normal, font, Pt(size))
    pf = normal.paragraph_format
    pf.line_spacing = 1.5
    pf.first_line_indent = Cm(1.25)
    pf.space_before = pf.space_after = Pt(0)
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    for level, extra in ((1, 2), (2, 1), (3, 0)):
        st = doc.styles[f"Heading {level}"]
        _set_font(st, font, Pt(size + extra), bold=True)
        st.font.italic = False
        hf = st.paragraph_format
        hf.first_line_indent = Cm(1.25)
        hf.left_indent = Cm(0)
        hf.alignment = WD_ALIGN_PARAGRAPH.LEFT
        hf.space_before = Pt(12)
        hf.space_after = Pt(6)
        hf.line_spacing = 1.5
        hf.keep_with_next = True
    for name in ("List Bullet", "List Number"):
        st = _ensure_style(doc, name)
        st.paragraph_format.first_line_indent = Cm(0)
        st.paragraph_format.left_indent = Cm(1.25 + 0.6)
    caption = _ensure_style(doc, "Caption")
    _set_font(caption, font, Pt(size), bold=False)
    caption.paragraph_format.first_line_indent = Cm(0)
    caption.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    caption.paragraph_format.keep_with_next = True
    caption.paragraph_format.space_before = Pt(6)
    table_text = _ensure_style(doc, "Table Text")
    _set_font(table_text, font, Pt(max(size - 2, 10)))
    tf = table_text.paragraph_format
    tf.first_line_indent = Cm(0)
    tf.line_spacing = 1.0
    tf.alignment = WD_ALIGN_PARAGRAPH.LEFT
    for level in (1, 2, 3):
        toc = _ensure_style(doc, f"TOC {level}")
        toc.paragraph_format.first_line_indent = Cm(0)
        toc.paragraph_format.left_indent = Cm(0.5 * (level - 1))
        toc.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        toc.paragraph_format.line_spacing = 1.15
        toc.paragraph_format.tab_stops.add_tab_stop(
            PAGE_TEXT_WIDTH, WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS
        )


def _ensure_report_styles(doc) -> None:
    """Стили, на которые опирается построитель, — в reference.docx их может не быть (документ Word
    с пустого листа хранит заголовки как «скрытые» стили). Заголовки — с уровнем структуры для TOC и
    закладок PDF."""
    for level in (1, 2, 3):
        name = f"Heading {level}"
        try:
            doc.styles[name]
        except KeyError:
            st = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            st.base_style = doc.styles["Normal"]
            st.font.bold = True
            st.paragraph_format.keep_with_next = True
            ppr = st.element.get_or_add_pPr()
            outline = OxmlElement("w:outlineLvl")
            outline.set(qn("w:val"), str(level - 1))
            ppr.append(outline)
    for name in ("List Bullet", "List Number", "Caption", "Table Text", "TOC 1", "TOC 2", "TOC 3"):
        _ensure_style(doc, name)


def _page_setup(section) -> None:
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.left_margin, section.right_margin = Mm(30), Mm(15)
    section.top_margin, section.bottom_margin = Mm(20), Mm(20)
    section.header_distance = section.footer_distance = Mm(10)


def _field(paragraph, instr: str, placeholder: str = "1") -> None:
    def fld(kind: str) -> None:
        el = OxmlElement("w:fldChar")
        el.set(qn("w:fldCharType"), kind)
        paragraph.add_run()._r.append(el)

    fld("begin")
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = f" {instr} "
    paragraph.add_run()._r.append(instr_el)
    fld("separate")
    paragraph.add_run(placeholder)
    fld("end")


_WATERMARK = (
    '<w:p {ns}><w:pPr><w:pStyle w:val="Header"/></w:pPr><w:r><w:pict>'
    '<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800" '
    'path="m@7,l@8,m@5,21600l@6,21600e">'
    '<v:formulas><v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/>'
    '<v:f eqn="sum 0 0 @2"/><v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/><v:f eqn="if @0 21600 @1"/>'
    '<v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/><v:f eqn="mid @5 @6"/><v:f eqn="mid @8 @5"/>'
    '<v:f eqn="mid @7 @8"/><v:f eqn="mid @6 @7"/><v:f eqn="sum @6 0 @5"/></v:formulas>'
    '<v:path textpathok="t" o:connecttype="custom" o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" '
    'o:connectangles="270,180,90,0"/><v:textpath on="t" fitshape="t"/>'
    '<v:handles><v:h position="#0,bottomRight" xrange="6629,14971"/></v:handles>'
    '<o:lock v:ext="edit" text="t" shapetype="t"/></v:shapetype>'
    '<v:shape id="PowerPlusWaterMarkObject" o:spid="_x0000_s2049" type="#_x0000_t136" '
    'style="position:absolute;margin-left:0;margin-top:0;width:470pt;height:110pt;rotation:315;'
    "z-index:-251657216;mso-position-horizontal:center;mso-position-horizontal-relative:margin;"
    'mso-position-vertical:center;mso-position-vertical-relative:margin" o:allowincell="f" '
    'fillcolor="#d9d9d9" stroked="f"><v:fill opacity=".5"/>'
    '<v:textpath style="font-family:&quot;{font}&quot;;font-size:1pt" on="t" fitshape="t" string="{text}"/>'
    "</v:shape></w:pict></w:r></w:p>"
)


def _watermark(header, text: str, font: str) -> None:
    ns = (
        nsdecls("w")
        + ' xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"'
    )
    quote = {'"': "&quot;"}
    header._element.append(
        parse_xml(_WATERMARK.format(ns=ns, font=escape(font, quote), text=escape(text, quote)))
    )


def _no_indent(paragraph, align=WD_ALIGN_PARAGRAPH.LEFT):
    paragraph.paragraph_format.first_line_indent = Cm(0)
    paragraph.alignment = align
    return paragraph


# --- построитель ---


@dataclass
class TocEntry:
    level: int
    text: str
    key: str = ""  # текст заголовка в документе (закладка PDF), если отличается от строки содержания

    @property
    def match(self) -> str:
        return self.key or self.text


@dataclass
class Builder:
    data: ReportData
    pages: dict[int, int] | None = None  # номер записи содержания → страница (второй проход)
    doc: object = None
    cite: Citations = None  # type: ignore[assignment]
    toc: list[TocEntry] = field(default_factory=list)
    tables: int = 0
    figures: int = 0

    def __post_init__(self) -> None:
        self.cite = Citations(self.data.sources)
        if self.data.reference_docx:
            self.doc = Document(io.BytesIO(self.data.reference_docx))
            body = self.doc.element.body
            for child in list(body):
                if child.tag != qn("w:sectPr"):
                    body.remove(child)
            _ensure_report_styles(self.doc)  # шаблон без нужных стилей не должен ронять сборку
        else:
            self.doc = Document()
            _apply_gost_styles(
                self.doc, self.data.settings.default_font, self.data.settings.default_font_size
            )
        self.font = self.data.settings.default_font

    # базовые элементы

    def para(self, text: str = "", style: str | None = None, bold: bool = False, italic: bool = False):
        p = self.doc.add_paragraph(style=self._style(style) if style else None)
        if text:
            self.inline(p, text, bold=bold, italic=italic)
        return p

    def _style(self, name: str):
        try:
            return self.doc.styles[name]
        except KeyError:
            return self.doc.styles["Normal"]

    def inline(self, p, text: str, bold: bool = False, italic: bool = False) -> None:
        """**полужирный**, *курсив*, `код` → обычный; ссылки [S12] → [n]."""
        text = self.cite.text(text)
        for part in re.split(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)", text):
            if not part:
                continue
            if part.startswith("**") and part.endswith("**"):
                run = p.add_run(part[2:-2])
                run.bold = True
            elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                run = p.add_run(part[1:-1])
                run.italic = True
            elif part.startswith("`"):
                run = p.add_run(part.strip("`"))
            else:
                run = p.add_run(part)
            run.bold = run.bold or bold or None
            run.italic = run.italic or italic or None

    def heading(self, text: str, level: int, *, new_page: bool = False, center: bool = False) -> None:
        if new_page:
            self.page_break()
        p = self.doc.add_heading(level=level)
        p.add_run(xml_safe(text))
        if center:
            _no_indent(p, WD_ALIGN_PARAGRAPH.CENTER)
        self.toc.append(TocEntry(level, text))

    def structural(self, text: str, *, toc: bool = True) -> None:
        """СПИСОК ИСПОЛНИТЕЛЕЙ, СОДЕРЖАНИЕ — прописными по центру, с новой страницы."""
        self.page_break()
        if toc:
            self.heading(text, 1, center=True)
        else:
            p = _no_indent(self.para(), WD_ALIGN_PARAGRAPH.CENTER)
            run = p.add_run(text)
            run.bold = True

    def page_break(self) -> None:
        body = self.doc.element.body
        if len(body) <= 1:  # пустой документ
            return
        self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    def table(self, title: str, columns: list[str], rows: list[list[str]], widths: list[float] | None = None):
        self.tables += 1
        cap = self.para(style="Caption")
        cap.add_run(f"{_t('item.table')} {self.tables} – {self.cite.text(title)}")
        ncols = max([len(columns)] + [len(r) for r in rows]) or 1
        tbl = self.doc.add_table(rows=1, cols=ncols)
        tbl.style = self._table_style()
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        hdr = tbl.rows[0]
        _repeat_header(hdr)
        for i in range(ncols):
            cell = hdr.cells[i]
            cell.paragraphs[0].style = self._style("Table Text")
            run = cell.paragraphs[0].add_run(xml_safe(columns[i]) if i < len(columns) else "")
            run.bold = True
        for row in rows:
            cells = tbl.add_row().cells
            for i in range(ncols):
                cells[i].paragraphs[0].style = self._style("Table Text")
                self.inline(cells[i].paragraphs[0], str(row[i]) if i < len(row) else "")
        _set_widths(tbl, widths or [1.0] * ncols)
        self.para()  # отступ после таблицы
        return tbl

    def _table_style(self):
        try:
            return self.doc.styles["Table Grid"]
        except KeyError:
            return None

    def markdown(self, text: str) -> None:
        """Markdown ответа агента: абзацы, списки, подзаголовки, таблицы."""
        lines = (text or "").replace("\r\n", "\n").split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            stripped = line.strip()
            if not stripped:
                i += 1
                continue
            if stripped.startswith("|"):
                block = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    block.append(lines[i].strip())
                    i += 1
                rows = [[c.strip() for c in r.strip("|").split("|")] for r in block]
                rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r if c)]
                if rows:
                    self._md_table(rows)
                continue
            if m := re.match(r"#{1,6}\s+(.*)", stripped):
                p = self.para()
                p.paragraph_format.keep_with_next = True
                self.inline(p, m.group(1), bold=True)
            elif m := re.match(r"[-*•]\s+(.*)", stripped):
                self.para(m.group(1), style="List Bullet")
            elif m := re.match(r"\d+[.)]\s+(.*)", stripped):
                self.para(m.group(1), style="List Number")
            else:
                buf = [stripped]
                while (
                    i + 1 < len(lines)
                    and lines[i + 1].strip()
                    and not re.match(r"(#{1,6}\s|[-*•]\s|\d+[.)]\s|\|)", lines[i + 1].strip())
                ):
                    i += 1
                    buf.append(lines[i].strip())
                self.para(" ".join(buf))
            i += 1

    def _md_table(self, rows: list[list[str]]) -> None:
        tbl = self.doc.add_table(rows=0, cols=max(len(r) for r in rows))
        tbl.style = self._table_style()
        for n, r in enumerate(rows):
            cells = tbl.add_row().cells
            for i, value in enumerate(r):
                cells[i].paragraphs[0].style = self._style("Table Text")
                self.inline(cells[i].paragraphs[0], value, bold=n == 0)
        _repeat_header(tbl.rows[0])
        _set_widths(tbl, [1.0] * len(tbl.columns))
        self.para()

    # части отчёта

    def build(self) -> bytes:
        d = self.data
        section = self.doc.sections[0]
        _page_setup(section)
        section.different_first_page_header_footer = True
        self.title_page()
        self.executors()
        self.contents()
        for node in d.tree:
            if node.enabled:
                self.section(node)
        self.appendices()
        self.headers_footers()
        _update_fields_on_open(self.doc)
        out = io.BytesIO()
        self.doc.save(out)
        return out.getvalue()

    def title_page(self) -> None:
        d = self.data
        if d.logo:
            p = _no_indent(self.para(), WD_ALIGN_PARAGRAPH.CENTER)
            p.add_run().add_picture(io.BytesIO(d.logo), height=Cm(2))
        p = _no_indent(self.para(), WD_ALIGN_PARAGRAPH.CENTER)
        p.add_run(xml_safe(d.executor_name.upper())).bold = True
        if d.executor_bin:
            _no_indent(self.para(f"БИН {d.executor_bin}"), WD_ALIGN_PARAGRAPH.CENTER)
        for _ in range(2):
            self.para()
        lead = next((m.full_name for m in d.members if m.role == "lead"), "")
        for text in (
            _t("title.approve"),
            _t("title.approve_role"),
            f"{_t('title.signature')} {lead}".rstrip(),
            _t("title.date_blank", year=d.created.year),
        ):
            p = self.para(text)
            p.paragraph_format.first_line_indent = Cm(0)
            p.paragraph_format.left_indent = Cm(9)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for _ in range(4):
            self.para()
        p = _no_indent(self.para(), WD_ALIGN_PARAGRAPH.CENTER)
        run = p.add_run(_t("title.report_word"))
        run.bold, run.font.size = True, Pt(d.settings.default_font_size + 4)
        _no_indent(self.para(_t("title.subtitle")), WD_ALIGN_PARAGRAPH.CENTER)
        _no_indent(self.para(f"«{d.project_name}»", bold=True), WD_ALIGN_PARAGRAPH.CENTER)
        self.para()
        customer = d.customer_name + (f" (БИН {d.customer_bin})" if d.customer_bin else "")
        _no_indent(self.para(f"{_t('title.customer')}: {customer}"), WD_ALIGN_PARAGRAPH.CENTER)
        status = "" if d.released else " (черновик)"
        _no_indent(self.para(f"{_t('title.version')} {d.version}{status}"), WD_ALIGN_PARAGRAPH.CENTER)
        if not d.released:
            p = _no_indent(self.para(), WD_ALIGN_PARAGRAPH.CENTER)
            run = p.add_run(_t("title.draft_mark"))
            run.bold, run.font.color.rgb = True, RGBColor(0xC0, 0x00, 0x00)
        for _ in range(6):
            self.para()
        city = _city(d.executor_address)
        _no_indent(self.para(f"{city} {d.created.year}".strip()), WD_ALIGN_PARAGRAPH.CENTER)

    def executors(self) -> None:
        d = self.data
        self.structural(_t("executors_heading"), toc=False)
        if not d.members:
            self.para(_t("executors_empty"))
            return
        roles = texts()["roles"]
        self.table(
            _t("executors_heading").capitalize(),
            texts()["executors_columns"],
            [
                [
                    m.full_name,
                    roles.get(m.role, m.role),
                    m.specialization or "—",
                    ", ".join(m.sections) or "—",
                    "",
                ]
                for m in d.members
            ],
            widths=[3, 2.6, 2.6, 1.6, 1.6],
        )

    def contents(self) -> None:
        self.structural(_t("toc_heading"), toc=False)
        self._toc_anchor = self.doc.add_paragraph()  # сюда вставляется поле содержания в конце сборки

    def section(self, node: TreeNode) -> None:
        d = self.data
        raw = d.raw_sections.get(node.id, {})
        self.heading(f"{node.id} {node.title}", 1, new_page=True)
        render = raw.get("render")
        if render == "toc_and_structure":
            self.para(_t("structure_intro"))
            for top in d.tree:
                if top.enabled:
                    self.para(f"{top.id} {top.title}", style="List Bullet")
            return
        if render == "quality_declaration":
            yn = texts()["yes_no"]

            def flag(key: str) -> str:
                v = d.independence.get(key)
                return yn["none" if v is None else str(bool(v)).lower()]

            self.para(
                _t(
                    "quality_declaration",
                    executor=d.executor_name,
                    affiliated=flag("affiliated"),
                    participated=flag("participated_in_docs"),
                    supplier=flag("is_supplier"),
                )
            )
            return
        if node.kind == "risks" and not node.children:
            if raw.get("requirement"):
                self.requirement(raw["requirement"])
            self.risks_section()
            return
        if node.kind == "conclusion" and not node.children:
            if raw.get("requirement"):
                self.requirement(raw["requirement"])
            if d.conclusions is None:
                self.para(_t("pending_section"), italic=True)
            else:
                self.payload(d.conclusions)
            return
        if raw.get("intro"):
            self.para(raw["intro"])
        for child in node.children:
            if not child.enabled:
                continue
            if child.type == "item":
                self.item(child, 2)
            else:
                self.heading(f"{child.id} {child.title}", 2)
                for item in child.children:
                    if item.enabled:
                        self.item(item, 3)

    def risks_section(self) -> None:
        d = self.data
        if not d.risks:
            self.para(_t("pending_section"), italic=True)
            return
        integ = d.integral
        zones = texts()["zones"]
        if integ is not None and integ.level:
            self.labeled(
                _t("risks.integral"),
                _t(
                    "risks.integral_text",
                    level=zones[integ.level],
                    red=integ.red,
                    yellow=integ.yellow,
                    green=integ.green,
                    scored=integ.scored,
                    total=integ.total,
                ),
            )
        groups = list(d.risk_groups) + sorted({r.group for r in d.risks} - set(d.risk_groups))
        cols = texts()["risks"]["columns"]
        for group in groups:
            lines = [r for r in d.risks if r.group == group]
            if not lines:
                continue
            self.heading(f"{group} {d.risk_groups.get(group, '')}".strip(), 2)
            rows = []
            for r in lines:
                mark = "" if r.mandatory else " " + _t("risks.extra_mark")
                if r.probability is None:
                    rows.append([f"{r.code}. {r.title}{mark}", "—", "—", "—", _t("risks.not_scored"), "—"])
                    continue
                rows.append(
                    [
                        f"{r.code}. {r.title}{mark}",
                        f"{d.probability_scale.get(r.probability, '')} ({r.probability})".strip(),
                        f"{d.impact_scale.get(r.impact, '')} ({r.impact})".strip(),
                        zones.get(r.zone, r.zone),
                        r.rationale,
                        "; ".join(r.mitigation),
                    ]
                )
            self.table(
                _t("risks.table", group=d.risk_groups.get(group, group).lower()),
                cols,
                rows,
                widths=[3.5, 2.4, 2.0, 1.9, 4.3, 3.0],
            )
        scored = [(r.code, r.probability, r.impact) for r in d.risks if r.probability]
        if scored:
            from app.report.charts import heatmap_png

            png = heatmap_png(d.zones, d.probability_scale, d.impact_scale, scored)  # type: ignore[arg-type]
            p = _no_indent(self.para(), WD_ALIGN_PARAGRAPH.CENTER)
            p.paragraph_format.keep_with_next = True
            p.add_run().add_picture(io.BytesIO(png), width=Cm(14))
            self.figures += 1
            cap = _no_indent(self.para(), WD_ALIGN_PARAGRAPH.CENTER)
            cap.add_run(f"{_t('risks.figure_word')} {self.figures} – {_t('risks.heatmap')}")

    def requirement(self, text: str) -> None:
        p = self.para()
        r = p.add_run(_t("item.requirement") + " ")
        r.italic = True
        r.bold = True
        p.add_run(" ".join(text.split())).italic = True

    def item(self, node: TreeNode, level: int) -> None:
        self.heading(f"{node.id} {node.title}", level)
        if node.requirement:
            self.requirement(node.requirement)
        res: ItemResult | None = self.data.items.get(node.id)
        payload = res.payload if res else None
        if payload is None:
            reason = (res.error if res and res.error else None) or _t("item.not_ready_default")
            self.para(_t("item.not_ready", reason=reason.split("\n")[0][:300].rstrip(".")), italic=True)
            return
        self.payload(payload)

    def labeled(self, label: str, text: str) -> None:
        p = self.para()
        p.add_run(f"{label}. ").bold = True
        self.inline(p, text)

    def payload(self, p: SectionPayload) -> None:
        if p.disclosure_status == "not_applicable":
            self.labeled(_t("item.conclusion"), p.summary or _t("item.not_applicable"))
            return
        if p.disclosure_status == "not_disclosed" and p.gap:
            self.para(_t("item.gap_heading") + ".", bold=True)
            for label, value in (
                (_t("item.gap_reason"), p.gap.reason),
                (_t("item.gap_impact"), p.gap.impact_on_conclusions),
                (_t("item.gap_assumptions"), p.gap.assumptions),
                (_t("item.gap_recommendation"), p.gap.recommendation),
            ):
                self.labeled(label, value)
            if p.analysis_md.strip():
                self.markdown(p.analysis_md)
            return
        if p.summary:
            self.labeled(_t("item.conclusion"), p.summary)
        self.markdown(p.analysis_md)
        for t in p.tables:
            self._result_table(t)
        for key, values in (
            ("item.remarks", p.remarks),
            ("item.recommendations", p.recommendations),
            ("item.assumptions", p.assumptions),
        ):
            if values:
                para = self.para()
                para.add_run(_t(key) + ":").bold = True
                para.paragraph_format.keep_with_next = True
                for v in values:
                    self.para(v, style="List Bullet")

    def _result_table(self, t: ResultTable) -> None:
        self.table(t.title or "", t.columns, [[str(c) for c in row] for row in t.rows])

    # приложения

    def appendices(self) -> None:
        for app in self.data.appendices:
            render = app.get("render")
            if render == "equipment_tables" and app.get("optional"):
                continue  # таблицы оборудования — в пунктах 3.4
            self.appendix_heading(str(app["id"]), app["title"])
            {"sources": self.app_sources, "coverage": self.app_coverage, "expert_cvs": self.app_cvs}.get(
                render, lambda: self.para(_t("equipment_note"))
            )()

    def appendix_heading(self, letter: str, title: str) -> None:
        self.page_break()
        text = f"{_t('appendix_word')} {letter}"
        p = self.doc.add_heading(level=1)
        p.add_run(text)
        _no_indent(p, WD_ALIGN_PARAGRAPH.CENTER)
        _no_indent(self.para(_t("appendix_kind")), WD_ALIGN_PARAGRAPH.CENTER)
        q = _no_indent(self.para(), WD_ALIGN_PARAGRAPH.CENTER)
        q.add_run(title).bold = True
        self.toc.append(TocEntry(1, f"{text} {title}", key=text))

    def app_sources(self) -> None:
        if not self.cite.order:
            self.para(_t("sources_empty"))
            return
        for n, sid in enumerate(self.cite.order, start=1):
            p = self.para(f"{n}. {_source_line(self.data.sources[sid])}")
            p.paragraph_format.first_line_indent = Cm(0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    def app_coverage(self) -> None:
        d = self.data
        labels = texts()["disclosure"]
        approved = texts()["coverage_approved"]
        experts = _experts_by_section(d)
        rows = []
        for node in walk_items(d.tree):
            res = d.items.get(node.id)
            p = res.payload if res else None
            status = labels[p.disclosure_status] if p else labels["missing"]
            nsrc = len({s.id for s in p.sources}) if p else 0
            rows.append(
                [
                    node.id,
                    node.title,
                    status,
                    str(nsrc),
                    experts.get(_section_of(node.id, experts), "—"),
                    approved["true" if res and res.approved else "false"],
                ]
            )
        for r in d.risks:
            rows.append(
                [
                    r.code,
                    r.title,
                    texts()["zones"][r.zone] if r.zone else labels["missing"],
                    "—",
                    experts.get(_section_of("4", experts), "—"),
                    approved["true" if r.approved else "false"],
                ]
            )
        if d.conclusions is not None:
            rows.append(
                [
                    "5",
                    "Выводы",
                    labels[d.conclusions.disclosure_status],
                    str(len(d.conclusions.sources)),
                    experts.get(_section_of("5", experts), "—"),
                    approved["true" if d.conclusions_approved else "false"],
                ]
            )
        self.table(
            "Статус пунктов технического задания",
            texts()["coverage_columns"],
            rows,
            widths=[1.6, 4.4, 2.2, 2.2, 2.4, 2.0],
        )

    def app_cvs(self) -> None:
        d = self.data
        if not d.members:
            self.para(_t("executors_empty"))
            return
        self.table(
            "Эксперты",
            texts()["cvs_columns"],
            [
                [
                    m.full_name,
                    m.specialization or "—",
                    m.education or "—",
                    m.research or "—",
                    str(m.years or "—"),
                ]
                for m in d.members
            ],
            widths=[2.6, 2.2, 2.6, 3.4, 1],
        )
        self.para(_t("cvs_note"))

    # колонтитулы, содержание

    def headers_footers(self) -> None:
        d = self.data
        s = self.doc.sections[0]
        mark = None if d.released else "ЧЕРНОВИК"
        for header in (s.header, s.first_page_header):
            if mark:
                _watermark(header, mark, self.font)
        footer = s.footer
        p = footer.paragraphs[0]
        _no_indent(p, WD_ALIGN_PARAGRAPH.CENTER)
        if d.settings.page_number_format == "page_of_total":
            p.add_run("Стр. ")
            _field(p, "PAGE")
            p.add_run(" из ")
            _field(p, "NUMPAGES")
        else:
            _field(p, "PAGE")
        note = footer.add_paragraph()
        _no_indent(note, WD_ALIGN_PARAGRAPH.CENTER)
        date = d.created.strftime("%d.%m.%Y")
        text = (
            _t("footer_released", version=d.version, date=date, hash=(d.sha256 or "")[:12])
            if d.released
            else _t("footer_draft", version=d.version, date=date)
        )
        run = note.add_run(text)
        run.font.size = Pt(8)
        self._fill_toc()

    def _fill_toc(self) -> None:
        """Поле TOC с заполненным результатом: Word обновит при открытии, LibreOffice покажет как есть.
        Номера страниц — из PDF первого прохода (закладки)."""
        anchor = self._toc_anchor
        begin = anchor
        _toc_field_start(begin)
        last = begin
        for n, entry in enumerate(self.toc):
            p = self.doc.add_paragraph(style=self._style(f"TOC {min(entry.level, 3)}"))
            p.add_run(entry.text)
            p.add_run("\t" + str((self.pages or {}).get(n, "")))
            last._p.addnext(p._p)
            last = p
        end = self.doc.add_paragraph()
        _fld_char(end, "end")
        last._p.addnext(end._p)


def _toc_field_start(p) -> None:
    _fld_char(p, "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-3" \\h \\z \\u '
    p.add_run()._r.append(instr)
    _fld_char(p, "separate")


def _fld_char(p, kind: str) -> None:
    el = OxmlElement("w:fldChar")
    el.set(qn("w:fldCharType"), kind)
    p.add_run()._r.append(el)


def _set_widths(tbl, widths: list[float]) -> None:
    """Фиксированные ширины столбцов во всю ширину текста (Word и LibreOffice берут их из tblGrid)."""
    tbl.autofit = False
    total = sum(widths)
    cols = [int(PAGE_TEXT_WIDTH * w / total) for w in widths[: len(tbl.columns)]]
    for i, w in enumerate(cols):
        tbl.columns[i].width = w
        for cell in tbl.columns[i].cells:
            cell.width = w


def _repeat_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _update_fields_on_open(doc) -> None:
    settings = doc.settings.element
    el = OxmlElement("w:updateFields")
    el.set(qn("w:val"), "true")
    settings.append(el)


def _city(address: str) -> str:
    m = re.search(r"\bг\.\s*([А-ЯЁA-Z][\w\-]+)", address or "")
    return m.group(1) if m else ""


def _source_line(s: SourceInfo) -> str:
    if s.type == "doc":
        where = (
            f"лист «{s.sheet}»" + (f", ячейки {s.cell_range}" if s.cell_range else "")
            if s.sheet
            else f"с. {s.page}"
            if s.page
            else ""
        )
        return f"{s.filename or s.title} (документ Заказчика){', ' + where if where else ''}."
    if s.type == "web":
        accessed = f" (дата обращения: {s.accessed.strftime('%d.%m.%Y')})" if s.accessed else ""
        return f"{s.title} [Электронный ресурс]. — URL: {s.url}{accessed}."
    if s.type == "reference":
        return f"{s.title}{', с. ' + str(s.page) if s.page else ''} (справочная библиотека Исполнителя)."
    return f"{s.title}."


def _experts_by_section(d: ReportData) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in d.members:
        for sec in m.sections:
            out.setdefault(sec, m.full_name)
    return out


def _section_of(item_id: str, sections: dict[str, str]) -> str:
    parts = item_id.split(".")
    for n in range(len(parts), 0, -1):
        key = ".".join(parts[:n])
        if key in sections:
            return key
    return ""


def render_docx(data: ReportData, pages: dict[int, int] | None = None) -> tuple[bytes, list[TocEntry]]:
    b = Builder(data, pages)
    return b.build(), b.toc
