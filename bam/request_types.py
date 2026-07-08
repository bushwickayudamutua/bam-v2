"""Catalog of request types.

The canonical labels below mirror the **production Airtable V2 base's**
single-select options verbatim (base ``appjIo54Z8MWrqhlI``, pulled
2026-07-06), e.g. ``"Jabón & Productos de baño / Soap & Shower Products /
肥皂和淋浴用品"``. Each type keeps a stable machine key, its category, and
its auto-expiration window (14 days standard, 30 days for Pots & Pans —
spec sections 2 and 4).

``SPEC_COMPAT`` holds a handful of extra types that appear in the spec text
(section 9) but not in the production base — kept so intake accepts them
from forms; their labels are spec-derived, not production strings.

``normalize_type`` accepts a key, a full label, any language segment of a
label, or an ``ITEM_ALIASES`` entry, so intake payloads and Airtable column
names from any form language resolve to the same canonical key.

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
    label: str  # "Español / English / 中文" (production string where available)
    category: str
    expiry_days: int = DEFAULT_EXPIRY_DAYS


_DEFAULT_GOODS: list[RequestType] = [
    # Food
    RequestType("groceries", "Alimentos / Groceries / 食品", "food"),
    RequestType("hot_meals", "Comida caliente / Hot meals / 热食", "food"),
    # Toiletries
    RequestType("baby_diapers", "Pañales / Baby Diapers / 嬰兒紙尿褲", "toiletries"),
    RequestType("adult_diapers", "Pañales de adultos / Adult Diapers / 成人紙尿褲", "toiletries"),
    RequestType("soap", "Jabón & Productos de baño / Soap & Shower Products / 肥皂和淋浴用品", "toiletries"),
    RequestType("pads", "Productos Femenino - Toallitas / Feminine Products - Pads / 衛生巾", "toiletries"),
    # Household
    RequestType("school_supplies", "Cosas de Escuela / School Supplies / 學校用品", "household"),
    RequestType("clothing", "Ropa / Clothing / 服裝", "household"),
    RequestType("stroller", "Coche / Stroller / 嬰兒車", "household"),
    # Furniture
    RequestType("sofa", "Sofa / Sofa / 沙發", "furniture"),
    RequestType("clothes_dresser", "Cajonera / Clothes Dresser / 衣櫃", "furniture"),
    RequestType("desk", "Escritorio / Desk /  書桌", "furniture"),
    RequestType("coffee_table", "Mesa de centro / Coffee Table / 咖啡桌", "furniture"),
    RequestType("chairs", "Sillas / Chairs / 椅子", "furniture"),
    RequestType("storage", "Almacenamiento / Storage / 儲物櫃", "furniture"),
    RequestType("dining_table", "Mesa Para Comedor / Dining Room Table / 餐桌", "furniture"),
    RequestType("refrigerator", "Nevera / Refrigerator / 冰箱", "furniture"),
    RequestType("air_conditioner", "Aire conditionador / Air Conditioner / 空調", "furniture"),
    RequestType("other_furniture", "Otras Muebles / Other Furniture / 其他家具", "furniture"),
    RequestType("crib", "Cuna / Crib / 嬰兒床", "furniture"),
    RequestType("twin_mattress", "Colchón individual / Twin Mattress / 單人床墊", "furniture"),
    RequestType("full_mattress", "Colchón matrimonio / Full Mattress / 雙人床墊", "furniture"),
    RequestType("queen_mattress", "Colchón tamaño Queen / Queen Mattress / 雙人加大床墊", "furniture"),
    RequestType("king_mattress", "Colchón tamaño King / King Mattress / 雙人特大床墊", "furniture"),
    RequestType("twin_mattress_frame", "Cama individual / Twin Mattress + Frame / 單人床墊+床架", "furniture"),
    RequestType("full_mattress_frame", "Cama matrimonio / Full Mattress + Frame / 雙人床墊+床架", "furniture"),
    RequestType("queen_mattress_frame", "Cama tamaño Queen / Queen Mattress + Frame / 雙人加大床墊+床架", "furniture"),
    RequestType("king_mattress_frame", "Cama tamaño King / King Mattress + Frame / 雙人特大床墊+床架", "furniture"),
    RequestType("twin_bed_frame", "Bastidor individual / Twin Bed Frame / 單人床架", "furniture"),
    RequestType("full_bed_frame", "Bastidor matrimonio / Full Bed Frame / 雙人床架", "furniture"),
    RequestType("queen_bed_frame", "Bastidor tamaño Queen / Queen Bed Frame / 雙人加大床架", "furniture"),
    RequestType("king_bed_frame", "Bastidor tamaño King / King Bed Frame / 雙人特大床架", "furniture"),
    RequestType("loft_bunk_bed", "Litera / Loft or Bunk Bed / 閣樓床或上下床", "furniture"),
    # Kitchen
    RequestType("microwave", "Microondas / Microwave / 微波爐", "kitchen"),
    RequestType("pots_pans", "Ollas y Sartenes / Pots & Pans / 鍋碗瓢盆", "kitchen", expiry_days=EXTENDED_EXPIRY_DAYS),
    RequestType("plates", "Platos / Plates / 盤子", "kitchen"),
    RequestType("cups", "Tazas / Cups / 杯子", "kitchen"),
    RequestType("utensils", "Utensilios / Utensils / 餐具", "kitchen"),
    RequestType("coffee_maker", "Cafetera / Coffee Maker / 咖啡機", "kitchen"),
    RequestType("blender", "Licuadora / Blender / 攪拌機", "kitchen"),
    RequestType("other_kitchen", "Otras Cosas de Cocina / Other Kitchen Items / 其他廚房用品", "kitchen"),
]

_DEFAULT_SOCIAL_SERVICES: list[RequestType] = [
    RequestType("tenant_legal", "Asistencia legal de inquilinos / Tenant legal assistance / 租戶法律協助", "social_service"),
    RequestType("in_school_services", "Asistencia con servicios escolares / Assistance with in-school services / 學校服務協助", "social_service"),
    RequestType("tutoring", "Tutoría estudiantil / Tutoring for students / 學生輔導", "social_service"),
    RequestType("english_classes", "Clases de inglés / English Classes / 英語課", "social_service"),
    RequestType("housing", "Asistencia asegurando vivienda / Securing housing / 住房協助", "social_service"),
    RequestType("health_insurance", "Asistencia con seguro médico / Medical insurance support / 醫療保險協助", "social_service"),
    RequestType("business_support", "Asistencia de Negocios / Small Business Support / 小型企業協助", "social_service"),
    RequestType("food_benefits", "Asistencia con beneficios de comida / Assistance with food benefits / 食品福利協助（WIC, SNAP, P-EBT）", "social_service"),
    RequestType("transportation", "Asistencia con Transporte / Transportation Assistance / 交通運輸協助", "social_service"),
    RequestType("child_disability", "Asistencia para niños con discapacidad / Assistance for disabled children / 殘疾兒童協助", "social_service"),
    RequestType("pet_assistance", "Asistencia para mascotas / Pet Assistance / 寵物協助", "social_service"),
    # Internet lives in two forms in production: the legacy low-cost-internet
    # service (a Fulfilled Request Count column) and the NYC Mesh install
    # pipeline (its own table, imported as mesh_internet).
    RequestType("internet", "Low-Cost Home Internet", "social_service"),
    RequestType("mesh_internet", "Mesh Internet Install", "social_service"),
]

#: Types the spec (section 9) names but the production base doesn't track.
#: Kept so intake accepts them from forms; labels are spec-derived.
_DEFAULT_SPEC_COMPAT: list[RequestType] = [
    RequestType("masks_covid_tests", "Mascarillas y pruebas de COVID / Masks & COVID Tests / 口罩和新冠检测", "household"),
    RequestType("pet_food", "Comida para mascotas / Pet Food / 宠物食品", "household"),
    RequestType("kitchen_supplies", "Artículos de cocina / Kitchen Supplies / 厨房用品", "kitchen"),
    RequestType("plates_cups_utensils", "Platos, vasos y cubiertos / Plates, Cups & Utensils", "kitchen"),
    RequestType("furniture", "Muebles / Furniture / 家具", "furniture"),
    RequestType("bed", "Cama / Bed / 床", "furniture"),
    RequestType("diapers_size_1", "Pañales talla 1 / Baby Diapers Size 1 / 婴儿尿布1号", "toiletries"),
    RequestType("diapers_size_2", "Pañales talla 2 / Baby Diapers Size 2 / 婴儿尿布2号", "toiletries"),
    RequestType("diapers_size_3", "Pañales talla 3 / Baby Diapers Size 3 / 婴儿尿布3号", "toiletries"),
    RequestType("diapers_size_4", "Pañales talla 4 / Baby Diapers Size 4 / 婴儿尿布4号", "toiletries"),
    RequestType("diapers_size_5", "Pañales talla 5 / Baby Diapers Size 5 / 婴儿尿布5号", "toiletries"),
    RequestType("diapers_size_6", "Pañales talla 6 / Baby Diapers Size 6 / 婴儿尿布6号", "toiletries"),
]

# Public catalog containers, populated by ``load_catalog`` (at import with the
# BAM defaults above, and again from an instance config at app startup). They
# are mutated IN PLACE so ``from bam.request_types import GOODS`` and the
# lookup functions below pick up an instance's custom catalog.
GOODS: list[RequestType] = []
SOCIAL_SERVICES: list[RequestType] = []
SPEC_COMPAT: list[RequestType] = []
ALL_TYPES: list[RequestType] = []
BY_KEY: dict[str, RequestType] = {}

#: The production base's Households.Languages select options, verbatim
#: (spec background section 6: 11 supported languages, plus Other). The
#: default; an instance config may replace it. Single source of truth for
#: intake and outreach language vocabularies — exposed via GET /catalog and
#: GET /config so the console views cannot drift.
_DEFAULT_LANGUAGES: list[str] = [
    "Inglés / English / 英文",
    "Español / Spanish / 西班牙语",
    "Chino Mandarín / Mandarin / 普通话",
    "Chino Cantonés / Cantonese / 广东话",
    "Chino Toishanés / Toishanese / 台山话",
    "Quechua el dialecto / Quechua Dialect / 克丘亞語",
    "Portugués / Portuguese / 葡萄牙語",
    "Criollo Haitiano / Haitian Creole / 法屬歸融語",
    "Tagalo/ Tagalog/ 他加禄语",
    "Árabe / Arabic / 阿拉伯語",
    "Francés / French / 法語",
    "Otro / Other / 其他語言",
]

LANGUAGES: list[str] = []  # populated by load_catalog

#: Alternate names that aren't already a label segment: item-level names
#: from the current forms plus common shorthand.
ITEM_ALIASES: dict[str, str] = {
    "dresser": "clothes_dresser",
    "fridge": "refrigerator",
    "ac": "air_conditioner",
    "dining table": "dining_table",
    "internet": "internet",
    "diapers": "baby_diapers",
    "pads/tampons/panty liners": "pads",
    "pads, tampons & panty liners": "pads",
}

#: Label segments from earlier form/spec revisions (the spec's section 9
#: vocabulary, simplified-Chinese variants) that differ from the production
#: select strings but mean the same type.
LEGACY_ALIASES: dict[str, str] = {
    # social services (spec wording)
    "housing": "housing",
    "vivienda": "housing",
    "住房": "housing",
    "health insurance": "health_insurance",
    "seguro médico": "health_insurance",
    "医疗保险": "health_insurance",
    "transportation": "transportation",
    "transporte": "transportation",
    "交通": "transportation",
    "tenant legal support": "tenant_legal",
    "ayuda legal para inquilinos": "tenant_legal",
    "租户法律援助": "tenant_legal",
    "in-school services": "in_school_services",
    "servicios escolares": "in_school_services",
    "校内服务": "in_school_services",
    "tutoring": "tutoring",
    "tutoría": "tutoring",
    "辅导": "tutoring",
    "business support": "business_support",
    "apoyo para negocios": "business_support",
    "商业支持": "business_support",
    "food benefits": "food_benefits",
    "beneficios de alimentos": "food_benefits",
    "食品补助": "food_benefits",
    "child disability support": "child_disability",
    "discapacidad infantil": "child_disability",
    "儿童残障支持": "child_disability",
    "互联网": "internet",
    # goods (spec wording / simplified-Chinese variants)
    "comida": "groceries",
    "食品杂货": "groceries",
    "toallas sanitarias, tampones y protectores": "pads",
    "卫生巾、卫生棉条和护垫": "pads",
    "卫生巾": "pads",
    "útiles escolares": "school_supplies",
    "学习用品": "school_supplies",
    "衣服": "clothing",
    "coche de bebé": "stroller",
    "婴儿车": "stroller",
    "成人尿布": "adult_diapers",
    "微波炉": "microwave",
    "咖啡机": "coffee_maker",
    "搅拌机": "blender",
    "锅碗瓢盆": "pots_pans",
    "热餐": "hot_meals",
    "hot meals": "hot_meals",
}

_BY_LABEL_SEGMENT: dict[str, RequestType] = {}  # populated by load_catalog


def load_catalog(
    goods: list[RequestType] | None = None,
    social_services: list[RequestType] | None = None,
    languages: list[str] | None = None,
) -> None:
    """(Re)build the catalog registries in place from the given lists, or the
    BAM defaults when a list is ``None``. Called at import with the defaults;
    an instance config re-invokes it at startup with a custom catalog. The
    module-level containers are mutated in place so already-imported names and
    the lookup functions below stay correct after a reconfigure.

    ``SPEC_COMPAT`` extras are BAM-specific and only included when ``goods`` is
    not overridden; the aliases likewise only apply to keys that exist.
    """
    # SPEC_COMPAT are BAM-only extras; keep them only when goods isn't overridden.
    extras = list(_DEFAULT_SPEC_COMPAT) if goods is None else []
    goods = list(_DEFAULT_GOODS if goods is None else goods)
    social = list(_DEFAULT_SOCIAL_SERVICES if social_services is None else social_services)

    GOODS[:] = goods
    SOCIAL_SERVICES[:] = social
    SPEC_COMPAT[:] = extras
    ALL_TYPES[:] = [*goods, *social, *extras]
    BY_KEY.clear()
    BY_KEY.update({t.key: t for t in ALL_TYPES})
    LANGUAGES[:] = list(_DEFAULT_LANGUAGES if languages is None else languages)

    _BY_LABEL_SEGMENT.clear()
    for _t in ALL_TYPES:
        _BY_LABEL_SEGMENT.setdefault(_t.label.lower(), _t)
        for _segment in _t.label.split(" / "):
            _BY_LABEL_SEGMENT.setdefault(_segment.strip().lower(), _t)
    for _aliases in (ITEM_ALIASES, LEGACY_ALIASES):
        for _alias, _key in _aliases.items():
            if _key in BY_KEY:
                _BY_LABEL_SEGMENT.setdefault(_alias, BY_KEY[_key])


load_catalog()  # populate with the BAM defaults at import


def normalize_type(value: str) -> str | None:
    """Resolve a key, label, label segment, or item alias to the canonical key.

    Trilingual inputs whose full label differs from the catalog's (an older
    form revision, a simplified/traditional character variant) still resolve
    if any of their segments matches a known segment or alias.
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
