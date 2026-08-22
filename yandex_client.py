"""
Клиент для API Поиска по организациям Яндекс.Карт (Places API).
Документация: https://yandex.ru/dev/maps/geosearch/

ВАЖНО:
  - Бесплатно, если в сутки не больше 500 запросов (без привязки карты).
  - В отличие от 2GIS, телефоны здесь отдаются бесплатно (поле Phones).
  - Официальной лицензии на сохранение/переиспользование этих данных нет —
    только на показ результата пользователю по его запросу (мы так и делаем:
    ничего не складываем в БД, просто пересылаем ответ в чат).
  - Telegram/WhatsApp как отдельных полей в документированном ответе нет —
    доступен только обычный номер телефона.

Ключ получают тут: https://yandex.ru/dev/maps/geosearch/ -> "Получить ключ"
(нужен обычный Яндекс-аккаунт, без карты).
"""

from urllib.parse import quote
import requests

from models import Place

BASE_URL = "https://search-maps.yandex.ru/v1/"

MAX_RESULTS_PER_PAGE = 10


class YandexApiError(Exception):
    pass


def _extract_phone(company_meta: dict) -> str | None:
    phones = company_meta.get("Phones") or []
    for p in phones:
        formatted = p.get("formatted")
        if formatted:
            return formatted
    return None


def _extract_rubric(company_meta: dict) -> str:
    categories = company_meta.get("Categories") or []
    names = [c.get("name") for c in categories if c.get("name")]
    return ", ".join(names[:2])


def _item_to_place(feature: dict) -> Place | None:
    props = feature.get("properties") or {}
    meta = props.get("CompanyMetaData")
    if not meta:
        return None  # это не организация (например, топоним) — пропускаем

    name = meta.get("name") or "Без названия"
    return Place(
        name=name,
        rubric=_extract_rubric(meta),
        address=meta.get("address") or "",
        rating="",  # рейтинг в этом API не документирован
        reviews_count=0,
        link=f"https://yandex.ru/maps/?text={quote(name)}",
        source="Яндекс",
        phone=_extract_phone(meta),
        has_site=bool(meta.get("url")),
    )


def search_places(niche: str, city: str, api_key: str, page: int = 1) -> tuple[list[Place], int]:
    """
    Ищет организации по нише и городу через API Поиска по организациям Яндекс.Карт.
    Возвращает (список Place, total_found).
    """
    skip = (page - 1) * MAX_RESULTS_PER_PAGE

    params = {
        "text": f"{niche}, {city}",
        "type": "biz",
        "lang": "ru_RU",
        "results": MAX_RESULTS_PER_PAGE,
        "skip": skip,
        "apikey": api_key,
    }

    try:
        resp = requests.get(BASE_URL, params=params, timeout=15)
    except requests.RequestException as e:
        raise YandexApiError(f"Не удалось связаться с Яндекс API: {e}") from e

    if resp.status_code == 403:
        raise YandexApiError(
            "Яндекс отклонил ключ (403). Проверьте, что ключ верный и подключён "
            "именно к сервису «API Поиска по организациям»."
        )
    if resp.status_code != 200:
        raise YandexApiError(f"Яндекс API вернул ошибку {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    features = data.get("features") or []
    total = (
        data.get("properties", {})
        .get("ResponseMetaData", {})
        .get("SearchResponse", {})
        .get("found", len(features))
    )

    places = [p for f in features if (p := _item_to_place(f)) is not None]
    return places, total
