"""
Объединяет результаты 2GIS, Яндекс.Карт и OpenStreetMap, применяет общие
фильтры и дедупликацию по user_id.

2GIS-клиент сам комбинирует поиск по тексту и по рубрике (это две разные
выборки данных 2GIS), Яндекс-клиент сам проходит все свои страницы, а OSM
(Overpass API) ищет по тегам или по названию — здесь просто объединяем то,
что они вернули, схлопываем сетевые точки одной компании, применяем фильтры
и сверяем с историей показанных.

ВРЕМЕННО: подробное логирование каждого источника + OSM вызывается первым
(было последним) — отлаживаем расхождение между прямым тестом (работает) и
живым ботом (OSM почему-то не долетает). Уберём/причешем после того, как
найдём причину.
"""

import logging

import gis_client
import osm_client
import storage
import yandex_client
from models import Place

logger = logging.getLogger(__name__)


def _collapse_chains(places: list[Place]) -> list[Place]:
    """
    Схлопывает разные точки одной сети (например, 5 адресов «Дринкит») в ОДНУ
    запись — иначе дедупликация по названию+адресу считает их разными
    компаниями (адрес-то у каждой точки свой) и показывает сеть по 5-8 раз.

    Группируем по названию (без учёта регистра/пробелов). Из нескольких точек
    одной сети оставляем ту, у которой есть хоть какой-то контакт (телефон,
    whatsapp или telegram) — это важнее всего для фильтра "с контактами";
    если контактов нигде нет — оставляем первую попавшуюся.

    Ограничение: это простое сравнение по точному названию, без учёта форм
    вроде "ООО" / кавычек — разные написания одной и той же сети («Дринкит»
    и «Drinkit») схлопнутся только если совпадают дословно.
    """

    def has_contact(p: Place) -> bool:
        return bool(p.phone or p.whatsapp or p.telegram)

    by_name: dict[str, Place] = {}
    for p in places:
        key = p.name.strip().lower()
        existing = by_name.get(key)
        if existing is None:
            by_name[key] = p
        elif not has_contact(existing) and has_contact(p):
            by_name[key] = p  # нашлась точка с контактом — она полезнее
    return list(by_name.values())


def search_new(
    niche: str,
    city: str,
    gis_api_key: str,
    yandex_api_key: str,
    has_site: bool | None = None,
    require_contact: bool = False,
    user_id: int | None = None,
) -> tuple[list[Place], int, list[str], int]:
    """
    Возвращает (новые Place, суммарный total по данным источников,
    предупреждения, кол-во кандидатов до дедупликации по истории —
    полезно, чтобы отличить "ничего не нашлось вообще" от "всё уже видели").
    """
    all_candidates: list[Place] = []
    total = 0
    warnings: list[str] = []

    logger.info("=== search_new старт: niche=%r city=%r has_site=%r user_id=%r ===",
                niche, city, has_site, user_id)

    # OSM/Overpass — без ключа, всегда пробуем. Поставлен ПЕРВЫМ (диагностика).
    logger.info("Вызываю OSM...")
    try:
        osm_places, osm_total = osm_client.search_places_all(niche, city)
        all_candidates.extend(osm_places)
        total += osm_total
        logger.info("OSM успешно вернул %d мест (total=%d)", len(osm_places), osm_total)
    except Exception as e:  # временно широкий except, чтобы увидеть ЛЮБУЮ ошибку
        logger.exception("OSM упал с исключением типа %s", type(e).__name__)
        warnings.append(f"OSM: {e}")

    if gis_api_key:
        logger.info("Вызываю 2GIS...")
        try:
            gis_places, gis_total = gis_client.search_places_all(
                niche, city, gis_api_key, has_site=has_site
            )
            all_candidates.extend(gis_places)
            total += gis_total
            logger.info("2GIS успешно вернул %d мест (total=%d)", len(gis_places), gis_total)
        except Exception as e:
            logger.exception("2GIS упал с исключением типа %s", type(e).__name__)
            warnings.append(f"2GIS: {e}")
    else:
        warnings.append("2GIS: не настроен (нет GIS_API_KEY)")

    if yandex_api_key:
        logger.info("Вызываю Яндекс...")
        try:
            ya_places, ya_total = yandex_client.search_places_all(niche, city, yandex_api_key)
            all_candidates.extend(ya_places)
            total += ya_total
            logger.info("Яндекс успешно вернул %d мест (total=%d)", len(ya_places), ya_total)
        except Exception as e:
            logger.exception("Яндекс упал с исключением типа %s", type(e).__name__)
            warnings.append(f"Яндекс: {e}")
    else:
        warnings.append("Яндекс: не настроен (нет YANDEX_API_KEY)")

    logger.info("Всего кандидатов до схлопывания сетей: %d", len(all_candidates))

    all_candidates = _collapse_chains(all_candidates)
    logger.info("После схлопывания сетей: %d", len(all_candidates))

    if has_site is False:
        # 2GIS уже отфильтрован на уровне запроса (has_site=false в API).
        # Для Яндекса и OSM фильтруем здесь по тому, что они сами определили
        # по полю url/website в ответе.
        all_candidates = [
            p for p in all_candidates
            if not (p.source in ("Яндекс", "OSM") and p.has_site)
        ]
        logger.info("После фильтра has_site=False: %d", len(all_candidates))

    if require_contact:
        all_candidates = [p for p in all_candidates if p.phone or p.whatsapp or p.telegram]
        logger.info("После фильтра require_contact: %d", len(all_candidates))

    before_dedup = len(all_candidates)

    if user_id is not None:
        new_places = storage.filter_unseen(user_id, all_candidates, city)
        logger.info("После дедупликации по истории (user_id=%s): %d", user_id, len(new_places))
    else:
        new_places = all_candidates

    logger.info("=== search_new конец: итого новых мест = %d ===", len(new_places))

    return new_places, total, warnings, before_dedup
