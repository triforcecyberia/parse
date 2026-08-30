"""
Клиент для Overpass API — открытого движка запросов по данным OpenStreetMap.
Документация: https://wiki.openstreetmap.org/wiki/Overpass_API

ВАЖНО:
  - Полностью бесплатно, БЕЗ ключа и регистрации.
  - Публичный сервер просит не превышать ~10 000 запросов/сутки и не грузить
    больше 5 ГБ/сутки — для личного бота с обычной частотой поисков это не
    проблема, но стоит иметь в виду, если вдруг начнёте гонять его в цикле.
  - Полнота данных по России обычно слабее, чем у 2GIS/Яндекса (карта
    заполняется волонтёрами), зато это независимый источник и единственный
    из трёх, где в принципе бывают теги contact:whatsapp / contact:telegram.
    Правда, на практике такие теги проставлены далеко не у всех объектов.
  - Общедоступный сервер иногда перегружен и может отвечать медленно или с
    таймаутом — это не баг клиента, а особенность бесплatного общего сервиса.

Как ищем:
  Сначала пробуем сопоставить нишу с обычными тегами OSM (amenity=cafe,
  shop=hairdresser и т.п.) по небольшому словарю NICHE_TAGS — так точнее.
  Если ниши в словаре нет — ищем по вхождению слова в название объекта
  (тег name) среди основных коммерческих категорий: это менее точно, но
  работает для произвольного текста ниши.
"""

import requests

from models import Place

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Небольшой словарь "ниша -> теги OSM". Не претендует на полноту — для ниш,
# которых здесь нет, используется запасной вариант (поиск по названию).
NICHE_TAGS: dict[str, list[tuple[str, str]]] = {
    "кофейня": [("amenity", "cafe")],
    "кафе": [("amenity", "cafe")],
    "ресторан": [("amenity", "restaurant")],
    "бар": [("amenity", "bar")],
    "паб": [("amenity", "pub")],
    "пекарня": [("shop", "bakery")],
    "стоматология": [("amenity", "dentist")],
    "аптека": [("amenity", "pharmacy")],
    "автосервис": [("shop", "car_repair")],
    "автомойка": [("shop", "car_wash"), ("amenity", "car_wash")],
    "парикмахерская": [("shop", "hairdresser")],
    "салон красоты": [("shop", "beauty")],
    "фитнес": [("leisure", "fitness_centre")],
    "спортзал": [("leisure", "fitness_centre")],
    "юрист": [("office", "lawyer")],
    "адвокат": [("office", "lawyer")],
    "химчистка": [("shop", "dry_cleaning")],
    "ветеринар": [("amenity", "veterinary")],
    "гостиница": [("tourism", "hotel")],
    "отель": [("tourism", "hotel")],
    "банк": [("amenity", "bank")],
    "супермаркет": [("shop", "supermarket")],
    "магазин одежды": [("shop", "clothes")],
    "книжный магазин": [("shop", "books")],
}

# Категории для запасного поиска по названию, если ниша не нашлась в словаре.
FALLBACK_TAG_KEYS = ["amenity", "shop", "office", "craft", "leisure", "tourism"]


class OsmApiError(Exception):
    pass


def _resolve_niche_tags(niche: str) -> list[tuple[str, str]] | None:
    niche_lower = niche.strip().lower()
    for key, tags in NICHE_TAGS.items():
        if key in niche_lower or niche_lower in key:
            return tags
    return None


def _build_query(niche: str, city: str) -> str:
    city_escaped = city.strip().replace('"', '\\"')
    tags = _resolve_niche_tags(niche)

    if tags:
        clauses = []
        for key, value in tags:
            clauses.append(f'node["{key}"="{value}"](area.searchArea);')
            clauses.append(f'way["{key}"="{value}"](area.searchArea);')
    else:
        # Запасной вариант: ищем по вхождению ниши в название среди основных
        # коммерческих категорий (регистронезависимо).
        niche_escaped = niche.strip().replace('"', '\\"')
        clauses = []
        for key in FALLBACK_TAG_KEYS:
            clauses.append(f'node["{key}"]["name"~"{niche_escaped}",i](area.searchArea);')
            clauses.append(f'way["{key}"]["name"~"{niche_escaped}",i](area.searchArea);')

    body = "\n  ".join(clauses)
    return (
        f'[out:json][timeout:25];\n'
        f'area["name"="{city_escaped}"]->.searchArea;\n'
        f"(\n  {body}\n);\n"
        f"out center 100;"
    )


def _extract_address(tags: dict) -> str:
    street = tags.get("addr:street", "")
    house = tags.get("addr:housenumber", "")
    if street and house:
        return f"{street}, {house}"
    return street or house or tags.get("addr:full", "")


def _element_to_place(element: dict) -> Place | None:
    tags = element.get("tags") or {}
    name = tags.get("name")
    if not name:
        return None  # объект без названия бесполезен для лидогенерации

    website = tags.get("website") or tags.get("contact:website")
    phone = tags.get("phone") or tags.get("contact:phone")
    whatsapp = tags.get("contact:whatsapp")
    telegram = tags.get("contact:telegram")

    rubric_value = next(
        (tags[k] for k in ("amenity", "shop", "office", "craft", "leisure", "tourism") if k in tags),
        "",
    )

    el_type = element.get("type", "node")
    el_id = element.get("id")

    return Place(
        name=name,
        rubric=rubric_value,
        address=_extract_address(tags),
        rating="",  # рейтингов в OSM нет
        reviews_count=0,
        link=f"https://www.openstreetmap.org/{el_type}/{el_id}",
        source="OSM",
        phone=phone,
        has_site=bool(website) if website is not None else None,
        whatsapp=whatsapp,
        telegram=telegram,
    )


def search_places_all(niche: str, city: str) -> tuple[list[Place], int]:
    """
    Ищет организации по нише и городу через Overpass API (OpenStreetMap).
    Ключ не нужен. Возвращает (список Place, кол-во найденных элементов).
    """
    query = _build_query(niche, city)

    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=30)
    except requests.RequestException as e:
        raise OsmApiError(f"Не удалось связаться с Overpass API: {e}") from e

    if resp.status_code == 429:
        raise OsmApiError(
            "Overpass API временно ограничил запросы с этого IP (429). "
            "Это общий бесплатный сервер — попробуйте через минуту-другую."
        )
    if resp.status_code != 200:
        raise OsmApiError(f"Overpass API вернул ошибку {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise OsmApiError(f"Overpass API вернул не-JSON ответ (возможно, перегружен): {e}") from e

    elements = data.get("elements") or []
    places = [p for el in elements if (p := _element_to_place(el)) is not None]

    return places, len(elements)
