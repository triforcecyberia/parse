"""
Общая модель организации, которую отдают оба клиента (2GIS и Яндекс.Карты).
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
    source: str                      # "2GIS" или "Яндекс"
    phone: Optional[str] = None      # телефон, если источник его отдаёт (пока только Яндекс)
    has_site: Optional[bool] = None  # True/False если известно, None если неизвестно (2GIS demo-ключ)

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
        lines.append(f"🔗 {self.link}")
        return "\n".join(lines)
