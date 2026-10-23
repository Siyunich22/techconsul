"""Паспорт проекта (TZ §4.5 Б): структурированная выжимка из документов, которую подтверждает эксперт.

Одна и та же схема — формат ответа LLM (map и reduce) и формат хранения/правки экспертом.
Источник поля — метки фрагментов `P12` (документ + страница/лист), которые извлечение раздаёт страницам;
поле без источника отбрасывается (CLAUDE.md, правило 2), если его не ввёл эксперт (`manual`).
"""

import uuid
from datetime import datetime
from typing import Literal, get_args, get_origin

from pydantic import BaseModel, Field

# --- значения ---


class Fact(BaseModel):
    """Скалярное поле паспорта."""

    value: str = Field(title="Значение")
    unit: str = Field(default="", title="Единица")
    refs: list[str] = Field(default_factory=list, description="Метки фрагментов-источников, например P12")
    quote: str = Field(default="", description="Дословная цитата из источника, до 200 символов")
    confidence: float = Field(default=1.0, ge=0, le=1)
    manual: bool = Field(default=False, description="Введено или изменено экспертом")


class Row(BaseModel):
    """Строка таблицы паспорта — источник и уверенность на уровне строки."""

    refs: list[str] = Field(default_factory=list)
    quote: str = ""
    confidence: float = Field(default=1.0, ge=0, le=1)
    manual: bool = False


Opt = Fact | None


# --- площадка и инфраструктура ---


class TechConditionRow(Row):
    resource: str = Field(default="", title="Ресурс")
    issuer: str = Field(default="", title="Кем выданы")
    number_date: str = Field(default="", title="Номер и дата")
    capacity: str = Field(default="", title="Разрешённая мощность / объём")
    validity: str = Field(default="", title="Срок действия / статус")


class Site(BaseModel):
    address: Opt = Field(default=None, title="Адрес / местоположение")
    coordinates: Opt = Field(default=None, title="Координаты")
    land_area: Opt = Field(default=None, title="Площадь участка")
    land_rights: Opt = Field(default=None, title="Право на землю")
    access_roads: Opt = Field(default=None, title="Подъездные пути")
    railway: Opt = Field(default=None, title="Железная дорога")
    power_supply: Opt = Field(default=None, title="Электроснабжение")
    gas_supply: Opt = Field(default=None, title="Газоснабжение")
    water_supply: Opt = Field(default=None, title="Водоснабжение")
    heat_supply: Opt = Field(default=None, title="Теплоснабжение")
    sewerage: Opt = Field(default=None, title="Водоотведение")
    technical_conditions: list[TechConditionRow] = Field(default_factory=list, title="Технические условия")


# --- технология и продукция ---


class ProgramRow(Row):
    year: str = Field(default="", title="Год / период")
    product: str = Field(default="", title="Продукция")
    volume: str = Field(default="", title="Объём")
    unit: str = Field(default="", title="Единица")
    load_pct: str = Field(default="", title="Загрузка мощности, %")


class Technology(BaseModel):
    product: Opt = Field(default=None, title="Продукция")
    capacity: Opt = Field(default=None, title="Производственная мощность")
    technology: Opt = Field(default=None, title="Технология")
    operating_mode: Opt = Field(default=None, title="Режим работы")
    staff: Opt = Field(default=None, title="Численность персонала")
    production_program: list[ProgramRow] = Field(default_factory=list, title="Производственная программа")


# --- оборудование ---


class EquipmentRow(Row):
    name: str = Field(default="", title="Наименование")
    quantity: str = Field(default="", title="Кол-во")
    specs: str = Field(default="", title="Характеристики")
    manufacturer: str = Field(default="", title="Производитель")
    country: str = Field(default="", title="Страна")
    cost: str = Field(default="", title="Стоимость")
    currency: str = Field(default="", title="Валюта")
    price_source: str = Field(default="", title="Источник цены")


class Equipment(BaseModel):
    items: list[EquipmentRow] = Field(default_factory=list, title="Перечень оборудования")


# --- сырьё и материалы ---


class MaterialRow(Row):
    material: str = Field(default="", title="Сырьё / материал")
    supplier: str = Field(default="", title="Поставщик")
    distance: str = Field(default="", title="Расстояние")
    annual_volume: str = Field(default="", title="Объём в год")
    consumption_rate: str = Field(default="", title="Норма расхода")


class Materials(BaseModel):
    items: list[MaterialRow] = Field(default_factory=list, title="Сырьё и материалы")


# --- энергоресурсы ---


class EnergyRow(Row):
    resource: str = Field(default="", title="Ресурс")
    demand: str = Field(default="", title="Потребность")
    consumption_rate: str = Field(default="", title="Норма расхода")
    source: str = Field(default="", title="Источник обеспечения")


class Energy(BaseModel):
    items: list[EnergyRow] = Field(default_factory=list, title="Энергоресурсы")


# --- бюджет ---


class CapexRow(Row):
    item: str = Field(default="", title="Статья")
    amount: str = Field(default="", title="Сумма")
    currency: str = Field(default="", title="Валюта")
    share: str = Field(default="", title="Доля, %")


class FinancingRow(Row):
    source: str = Field(default="", title="Источник финансирования")
    amount: str = Field(default="", title="Сумма")
    currency: str = Field(default="", title="Валюта")
    share: str = Field(default="", title="Доля, %")


class Budget(BaseModel):
    capex_total: Opt = Field(default=None, title="CAPEX, всего")
    opex_annual: Opt = Field(default=None, title="OPEX в год")
    maintenance_capex: Opt = Field(default=None, title="Поддерживающие капвложения")
    capex_items: list[CapexRow] = Field(default_factory=list, title="CAPEX по статьям")
    financing: list[FinancingRow] = Field(default_factory=list, title="Источники финансирования")


# --- график ---


class StageRow(Row):
    stage: str = Field(default="", title="Этап")
    start: str = Field(default="", title="Начало")
    end: str = Field(default="", title="Окончание")
    status: str = Field(default="", title="Статус")


class Schedule(BaseModel):
    construction_start: Opt = Field(default=None, title="Начало строительства")
    commissioning: Opt = Field(default=None, title="Ввод в эксплуатацию")
    investment_phase: Opt = Field(default=None, title="Длительность инвестиционной фазы")
    stages: list[StageRow] = Field(default_factory=list, title="Этапы инвестиционной фазы")


# --- закупки ---


class ProcurementRow(Row):
    subject: str = Field(default="", title="Предмет закупки")
    method: str = Field(default="", title="Способ / процедура")
    supplier: str = Field(default="", title="Поставщик / участники")
    contract_status: str = Field(default="", title="Статус договора")
    amount: str = Field(default="", title="Сумма")


class Procurement(BaseModel):
    policy: Opt = Field(default=None, title="Порядок закупок")
    items: list[ProcurementRow] = Field(default_factory=list, title="Закупки")


# --- экология ---


class Ecology(BaseModel):
    hazard_class: Opt = Field(default=None, title="Класс опасности / категория объекта")
    emission_sources: Opt = Field(default=None, title="Источники выбросов")
    sanitary_zone: Opt = Field(default=None, title="Санитарно-защитная зона")
    waste: Opt = Field(default=None, title="Отходы")
    ovos: Opt = Field(default=None, title="ОВОС")
    permits: Opt = Field(default=None, title="Заключения и разрешения")


class PassportData(BaseModel):
    site: Site = Field(default_factory=Site, title="Площадка и инфраструктура")
    technology: Technology = Field(default_factory=Technology, title="Технология и продукция")
    equipment: Equipment = Field(default_factory=Equipment, title="Оборудование")
    materials: Materials = Field(default_factory=Materials, title="Сырьё и материалы")
    energy: Energy = Field(default_factory=Energy, title="Энергоресурсы")
    budget: Budget = Field(default_factory=Budget, title="Бюджет проекта")
    schedule: Schedule = Field(default_factory=Schedule, title="График инвестиционной фазы")
    procurement: Procurement = Field(default_factory=Procurement, title="Закупки")
    ecology: Ecology = Field(default_factory=Ecology, title="Экология")


# --- расхождения ---


class DiscrepancyValue(BaseModel):
    value: str
    refs: list[str] = Field(default_factory=list)


class Discrepancy(BaseModel):
    field: str = Field(description="Путь поля паспорта, например technology.capacity")
    title: str = Field(description="Короткое название расхождения")
    description: str = Field(description="В чём противоречие, одно-два предложения с цифрами")
    severity: Literal["high", "medium", "low"] = "medium"
    values: list[DiscrepancyValue] = Field(default_factory=list)
    resolution: str = Field(default="", description="Комментарий эксперта (заполняет эксперт)")


class ReducedPassport(BaseModel):
    """Ответ шага сведения: итоговый паспорт и противоречия между документами."""

    passport: PassportData
    discrepancies: list[Discrepancy] = Field(default_factory=list)


# --- макет для интерфейса (генерируется из схемы — без дублирования подписей во фронтенде) ---


class LayoutColumn(BaseModel):
    key: str
    title: str


class LayoutField(BaseModel):
    key: str
    title: str


class LayoutTable(BaseModel):
    key: str
    title: str
    columns: list[LayoutColumn]


class LayoutSection(BaseModel):
    key: str
    title: str
    fields: list[LayoutField]
    tables: list[LayoutTable]


def _row_model(annotation) -> type[Row] | None:
    if get_origin(annotation) is list:
        (arg,) = get_args(annotation)
        if isinstance(arg, type) and issubclass(arg, Row):
            return arg
    return None


def passport_layout() -> list[LayoutSection]:
    sections = []
    for key, info in PassportData.model_fields.items():
        model: type[BaseModel] = info.annotation  # type: ignore[assignment]
        fields, tables = [], []
        for fkey, finfo in model.model_fields.items():
            row = _row_model(finfo.annotation)
            if row is None:
                fields.append(LayoutField(key=fkey, title=finfo.title or fkey))
                continue
            columns = [
                LayoutColumn(key=c, title=ci.title or c)
                for c, ci in row.model_fields.items()
                if c not in Row.model_fields
            ]
            tables.append(LayoutTable(key=fkey, title=finfo.title or fkey, columns=columns))
        sections.append(LayoutSection(key=key, title=info.title or key, fields=fields, tables=tables))
    return sections


# --- API ---

PassportStatus = Literal["empty", "extracting", "draft", "confirmed", "failed"]


class SourceOut(BaseModel):
    document_id: uuid.UUID
    filename: str
    category: str | None = None
    page: int | None = None
    sheet: str | None = None


class PassportProgress(BaseModel):
    stage: Literal["map", "reduce", ""] = ""
    done: int = 0
    total: int = 0


class PassportOut(BaseModel):
    status: PassportStatus
    data: PassportData
    discrepancies: list[Discrepancy]
    sources: dict[str, SourceOut]
    progress: PassportProgress
    warnings: list[str]
    error: str | None = None
    extracted_at: datetime | None = None
    updated_at: datetime | None = None
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None  # ФИО


class PassportUpdate(BaseModel):
    data: PassportData
    discrepancies: list[Discrepancy] = Field(default_factory=list)
