"""
Общая модель организации, которую отдают клиенты (2GIS, Яндекс.Карты, OSM).
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Place:
    name: str
    rubric: str
    address: str
    rating: str
    reviews_count: int
    link: str
    source: str                      # "2GIS", "Яндекс" или "OSM"
    phone: Optional[str] = None      # телефон, если источник его отдаёт (Яндекс, иногда OSM)
    has_site: Optional[bool] = None  # True/False если известно, None если неизвестно (2GIS demo-ключ)
    whatsapp: Optional[str] = None   # только OSM (тег contact:whatsapp) — редко встречается, но бывает
    telegram: Optional[str] = None   # только OSM (тег contact:telegram) — редко встречается, но бывает

    def to_text(self) -> str:
        lines = [f"🏢 <b>{self.name}</b>  <i>[{self.source}]</i>"]
        if self.rubric:
            lines.append(f"📁 {self.rubric}")
        if self.address:
            lines.append(f"📍 {self.address}")
        if self.rating:
            lines.append(f"⭐ {self.rating} ({self.reviews_count} отзывов)")
        if self.phone:
            lines.append(f"📞 {self.phone}")
        if self.whatsapp:
            lines.append(f"💬 WhatsApp: {self.whatsapp}")
        if self.telegram:
            lines.append(f"✈️ Telegram: {self.telegram}")
        lines.append(f"🔗 {self.link}")
        return "\n".join(lines)
