"""
Объединяет результаты 2GIS и Яндекс.Карт, применяет общие фильтры.

Про фильтр require_contact=True:
  2GIS на demo-ключе телефон не отдаёт (Place.phone всегда None), поэтому
  такие карточки этим фильтром отсеиваются автоматически — контакт есть
  только у части результатов из Яндекса. Это не баг, а честное следствие
  того, что 2GIS прячет контакты за платной подпиской.
"""

import gis_client
import yandex_client
from models import Place


class SearchError(Exception):
    """Собранные ошибки источников (не всегда фатально — один источник может упасть, другой сработать)."""

    def __init__(self, messages: list[str]):
        self.messages = messages
        super().__init__("; ".join(messages))


def search_all(
    niche: str,
    city: str,
    gis_api_key: str,
    yandex_api_key: str,
    page: int = 1,
    has_site: bool | None = None,
    require_contact: bool = False,
) -> tuple[list[Place], int, list[str]]:
    """
    Возвращает (отфильтрованный список Place, суммарный total по данным API, список предупреждений).
    """
    places: list[Place] = []
    total = 0
    warnings: list[str] = []

    if gis_api_key:
        try:
            gis_places, gis_total = gis_client.search_places(
                niche, city, gis_api_key, page=page, has_site=has_site
            )
            places.extend(gis_places)
            total += gis_total
        except gis_client.GisApiError as e:
            warnings.append(f"2GIS: {e}")
    else:
        warnings.append("2GIS: не настроен (нет GIS_API_KEY)")

    if yandex_api_key:
        try:
            ya_places, ya_total = yandex_client.search_places(niche, city, yandex_api_key, page=page)
            places.extend(ya_places)
            total += ya_total
        except yandex_client.YandexApiError as e:
            warnings.append(f"Яндекс: {e}")
    else:
        warnings.append("Яндекс: не настроен (нет YANDEX_API_KEY)")

    if has_site is False:
        # 2GIS уже отфильтрован на уровне запроса (has_site=false в API),
        # для Яндекса фильтруем здесь по полю url в ответе.
        places = [p for p in places if not (p.source == "Яндекс" and p.has_site)]

    if require_contact:
        places = [p for p in places if p.phone]

    return places, total, warnings
