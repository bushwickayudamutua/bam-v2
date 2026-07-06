"""Catalog of request types.

Airtable stores request types as trilingual single-select strings, e.g.
``"Jabón & Productos de baño / Soap & Shower Products / 肥皂和淋浴用品"``.
This module keeps a stable machine key per type plus that display label, the
category, and the auto-expiration window (14 days standard, 30 days for
Pots & Pans — spec sections 2 and 4).

``normalize_type`` accepts either a key or any language segment of a label so
intake payloads from any form language resolve to the same canonical key;
``ITEM_ALIASES`` additionally maps the item-level names the current forms use
(background section 5) onto their catalog type.

The catalog's ``expiry_days`` values mark each type's expiration *tier*;
``expiry_days_for``/``default_expiry_days`` resolve the tier against
``settings`` so ``BAM_DEFAULT_EXPIRY_DAYS``/``BAM_EXTENDED_EXPIRY_DAYS``
overrides take effect everywhere windows are computed.
"""

from __future__ import annotations

from dataclasses import dataclass

from bam.config import settings

DEFAULT_EXPIRY_DAYS = 14
EXTENDED_EXPIRY_DAYS = 30


@dataclass(frozen=True)
class RequestType:
    key: str
    label: str  # "Español / English / 中文"
    category: str
    expiry_days: int = DEFAULT_EXPIRY_DAYS


GOODS: list[RequestType] = [
    # Toiletries
    RequestType("soap", "Jabón & Productos de baño / Soap & Shower Products / 肥皂和淋浴用品", "toiletries"),
    RequestType("pads", "Toallas sanitarias, tampones y protectores / Pads, Tampons & Panty Liners / 卫生巾、卫生棉条和护垫", "toiletries"),
    RequestType("diapers_size_1", "Pañales talla 1 / Baby Diapers Size 1 / 婴儿尿布1号", "toiletries"),
    RequestType("diapers_size_2", "Pañales talla 2 / Baby Diapers Size 2 / 婴儿尿布2号", "toiletries"),
    RequestType("diapers_size_3", "Pañales talla 3 / Baby Diapers Size 3 / 婴儿尿布3号", "toiletries"),
    RequestType("diapers_size_4", "Pañales talla 4 / Baby Diapers Size 4 / 婴儿尿布4号", "toiletries"),
    RequestType("diapers_size_5", "Pañales talla 5 / Baby Diapers Size 5 / 婴儿尿布5号", "toiletries"),
    RequestType("diapers_size_6", "Pañales talla 6 / Baby Diapers Size 6 / 婴儿尿布6号", "toiletries"),
    RequestType("adult_diapers", "Pañales para adultos / Adult Diapers / 成人尿布", "toiletries"),
    # Household
    RequestType("clothing", "Ropa / Clothing / 衣服", "household"),
    RequestType("school_supplies", "Útiles escolares / School Supplies / 学习用品", "household"),
    RequestType("stroller", "Coche de bebé / Stroller / 婴儿车", "household"),
    RequestType("pet_food", "Comida para mascotas / Pet Food / 宠物食品", "household"),
    RequestType("masks_covid_tests", "Mascarillas y pruebas de COVID / Masks & COVID Tests / 口罩和新冠检测", "household"),
    # Kitchen
    RequestType("kitchen_supplies", "Artículos de cocina / Kitchen Supplies / 厨房用品", "kitchen"),
    RequestType("pots_pans", "Ollas y sartenes / Pots & Pans / 锅碗瓢盆", "kitchen", expiry_days=EXTENDED_EXPIRY_DAYS),
    RequestType("plates_cups_utensils", "Platos, vasos y cubiertos / Plates, Cups & Utensils / 餐具", "kitchen"),
    RequestType("microwave", "Microondas / Microwave / 微波炉", "kitchen"),
    RequestType("coffee_maker", "Cafetera / Coffee Maker / 咖啡机", "kitchen"),
    RequestType("blender", "Licuadora / Blender / 搅拌机", "kitchen"),
    # Food
    RequestType("groceries", "Comida / Groceries / 食品杂货", "food"),
    RequestType("hot_meals", "Comida caliente / Hot Meals / 热餐", "food"),
    # Furniture (separate team flow; requests carry a delivery address)
    RequestType("furniture", "Muebles / Furniture / 家具", "furniture"),
    RequestType("bed", "Cama / Bed / 床", "furniture"),
]

SOCIAL_SERVICES: list[RequestType] = [
    RequestType("housing", "Vivienda / Housing / 住房", "social_service"),
    RequestType("health_insurance", "Seguro médico / Health Insurance / 医疗保险", "social_service"),
    RequestType("english_classes", "Clases de inglés / English Classes / 英语课程", "social_service"),
    RequestType("transportation", "Transporte / Transportation / 交通", "social_service"),
    RequestType("tenant_legal", "Ayuda legal para inquilinos / Tenant Legal Support / 租户法律援助", "social_service"),
    RequestType("in_school_services", "Servicios escolares / In-School Services / 校内服务", "social_service"),
    RequestType("tutoring", "Tutoría / Tutoring / 辅导", "social_service"),
    RequestType("business_support", "Apoyo para negocios / Business Support / 商业支持", "social_service"),
    RequestType("internet", "Internet / Internet / 互联网", "social_service"),
    RequestType("food_benefits", "Beneficios de alimentos / Food Benefits / 食品补助", "social_service"),
    RequestType("child_disability", "Discapacidad infantil / Child Disability Support / 儿童残障支持", "social_service"),
    RequestType("pet_assistance", "Asistencia para mascotas / Pet Assistance / 宠物援助", "social_service"),
]

ALL_TYPES: list[RequestType] = GOODS + SOCIAL_SERVICES
BY_KEY: dict[str, RequestType] = {t.key: t for t in ALL_TYPES}

#: Item-level names from the current intake forms (background section 5) that
#: resolve to a broader catalog type. Item detail itself is preserved on the
#: request notes / raw submission by the intake service.
ITEM_ALIASES: dict[str, str] = {
    "plates": "plates_cups_utensils",
    "cups": "plates_cups_utensils",
    "utensils": "plates_cups_utensils",
    "sofa": "furniture",
    "dresser": "furniture",
    "desk": "furniture",
    "coffee table": "furniture",
    "chairs": "furniture",
    "storage": "furniture",
    "dining table": "furniture",
    "fridge": "furniture",
    "ac": "furniture",
}

_BY_LABEL_SEGMENT: dict[str, RequestType] = {}
for _t in ALL_TYPES:
    _BY_LABEL_SEGMENT[_t.label.lower()] = _t
    for _segment in _t.label.split(" / "):
        _BY_LABEL_SEGMENT.setdefault(_segment.strip().lower(), _t)
for _alias, _key in ITEM_ALIASES.items():
    _BY_LABEL_SEGMENT.setdefault(_alias, BY_KEY[_key])


def normalize_type(value: str) -> str | None:
    """Resolve a key, label segment, or item alias to the canonical key.

    Trilingual inputs whose full label differs from the catalog's (e.g. a
    base-specific select like "Mesa de centro / Coffee Table / 咖啡桌") still
    resolve if any of their segments matches a known segment or alias.
    """
    if not value:
        return None
    candidate = value.strip()
    if candidate in BY_KEY:
        return candidate
    match = _BY_LABEL_SEGMENT.get(candidate.lower())
    if match is not None:
        return match.key
    for segment in candidate.split(" / "):
        match = _BY_LABEL_SEGMENT.get(segment.strip().lower())
        if match is not None:
            return match.key
    return None


def get_request_type(key: str) -> RequestType:
    return BY_KEY[key]


def label_for(key: str) -> str:
    rt = BY_KEY.get(key)
    return rt.label if rt else key


def default_expiry_days() -> int:
    """The standard (settings-overridable) expiration window."""
    return settings.default_expiry_days


def expiry_days_for(key: str) -> int:
    """Expiration window for a type, resolved against settings overrides."""
    rt = BY_KEY.get(key)
    if rt is not None and rt.expiry_days == EXTENDED_EXPIRY_DAYS:
        return settings.extended_expiry_days
    return settings.default_expiry_days


def is_social_service(key: str) -> bool:
    rt = BY_KEY.get(key)
    return rt is not None and rt.category == "social_service"


def goods_keys() -> list[str]:
    return [t.key for t in GOODS]


def social_service_keys() -> list[str]:
    return [t.key for t in SOCIAL_SERVICES]
