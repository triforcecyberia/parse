"""
Клиент для официального 2GIS Catalog API (Places API, v3.0).

ВАЖНО про лимиты бесплатного demo-ключа (см. docs.2gis.com):
  - page_size (объектов на странице) — максимум 10
  - page (номер страницы) — максимум 5
  - контактные данные (телефон, whatsapp и т.п.) НЕ отдаются на demo-ключе,
    это платная функция. Здесь их и не запрашиваем — только открытые поля:
    название, адрес, категория (рубрика), рейтинг, координаты.

Ключ получают тут: https://dev.2gis.com -> "Получить демо-ключ".
"""

from urllib.parse import quote
import requests

from models import Place

BASE_URL = "https://catalog.api.2gis.com/3.0/items"

MAX_PAGE_SIZE = 10   # лимит demo-ключа
MAX_PAGE = 5          # лимит demo-ключа


class GisApiError(Exception):
    pass


def _extract_address(item: dict) -> str:
    addr = item.get("address_name") or ""
    comment = item.get("address_comment")
    if comment:
        addr = f"{addr}, {comment}" if addr else comment
    return addr


def _extract_rubric(item: dict) -> str:
    rubrics = item.get("rubrics") or []
    names = [r.get("name") for r in rubrics if r.get("name")]
    return ", ".join(names[:2])


def _extract_rating(item: dict):
    reviews = item.get("reviews") or {}
    rating = reviews.get("general_rating")
    count = reviews.get("general_review_count") or 0
    if rating:
        return f"{float(rating):.1f}", count
    return "", count


def _item_to_place(item: dict, has_site_filter: bool | None) -> Place:
    rating, count = _extract_rating(item)
    name = item.get("name") or "Без названия"
    return Place(
        name=name,
        rubric=_extract_rubric(item),
        address=_extract_address(item),
        rating=rating,
        reviews_count=count,
        link=f"https://2gis.ru/search/{quote(name)}",
        source="2GIS",
        phone=None,  # 2GIS отдаёт контакты только по платной подписке
        has_site=has_site_filter,  # если фильтр применялся на уровне запроса — он и есть ответ
    )


def search_places(
    niche: str,
    city: str,
    api_key: str,
    page: int = 1,
    has_site: bool | None = None,
) -> tuple[list[Place], int]:
    """
    Ищет организации по нише и городу через официальный 2GIS Catalog API.
    Возвращает (список Place, total_count_по_данным_api).

    Город передаём прямо в тексте запроса (без отдельного резолвинга city_id) —
    так проще и надёжнее работает на demo-ключе.

    has_site:
        None  — не фильтровать (показывать всех)
        False — показывать только организации БЕЗ сайта
        True  — показывать только организации С сайтом
    Фильтр применяется на стороне 2GIS API (параметр has_site из их
    документации), а не на нашей стороне — так что фильтрация точная и не
    зависит от полей, недоступных на demo-ключе.
    """
    if page < 1 or page > MAX_PAGE:
        raise GisApiError(f"Demo-ключ поддерживает страницы только с 1 по {MAX_PAGE}.")

    params = {
        "q": f"{niche}, {city}",
        "type": "branch",
        "page": page,
        "page_size": MAX_PAGE_SIZE,
        "fields": "items.rubrics,items.reviews,items.address_comment",
        "key": api_key,
    }

    if has_site is not None:
        params["has_site"] = "true" if has_site else "false"

    try:
        resp = requests.get(BASE_URL, params=params, timeout=15)
    except requests.RequestException as e:
        raise GisApiError(f"Не удалось связаться с 2GIS API: {e}") from e

    if resp.status_code == 403:
        raise GisApiError(
            "2GIS отклонил ключ (403). Проверьте, что ключ верный и не истёк "
            "срок демо-доступа."
        )
    if resp.status_code != 200:
        raise GisApiError(f"2GIS API вернул ошибку {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    meta = data.get("meta") or {}
    if meta.get("error"):
        raise GisApiError(
            f"2GIS API вернул ошибку: {meta['error'].get('message', meta['error'])}. "
            "Если ошибка про параметр has_site — возможно, на demo-ключе он недоступен, "
            "тогда фильтрацию без сайта нужно будет делать иначе (например, вручную "
            "проверять сайт компании)."
        )
    result = data.get("result") or {}
    items = result.get("items") or []
    total = result.get("total", len(items))

    places = [_item_to_place(item, has_site) for item in items]
    return places, total
