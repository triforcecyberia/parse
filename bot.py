"""
Telegram-бот для поиска организаций по нише и городу через официальные API
2GIS, Яндекс.Карт и OpenStreetMap одновременно.

Особенности:
  - 2GIS: сам комбинирует поиск по тексту и по рубрике (категории) — это две
    разные выборки данных 2GIS, вместе они дают больше, чем потолок в 50 у
    одного отдельного запроса. Телефон/WhatsApp/Instagram у 2GIS всё равно
    только платно, поэтому у карточек из 2GIS телефона нет.
  - Яндекс.Карты: бесплатный API «Поиск по организациям», телефон отдаёт
    бесплатно — используем именно его для требования "с контактами".
  - Обязательный фильтр (если включён ниже): показываем только организации
    хотя бы с одним контактом (на практике — с телефоном; Telegram/WhatsApp
    как отдельные поля бесплатные API не отдают).
  - Дедупликация по аккаунту: компании, которые боту уже показывал этому
    Telegram-пользователю раньше (сохранено в SQLite, переживает перезапуск),
    повторно не присылаются. У каждого аккаунта своя история.

Запуск:
  1. pip install -r requirements.txt
  2. создать .env (см. .env.example) с TELEGRAM_BOT_TOKEN, GIS_API_KEY, YANDEX_API_KEY
  3. python bot.py
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import RetryAfter, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from aggregator import search_new
import storage

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GIS_API_KEY = os.environ.get("GIS_API_KEY", "")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "")

# Временно отключено: пока YANDEX_API_KEY не заработает, контактов почти ни
# у кого нет (2GIS их не отдаёт бесплатно), и фильтр всё обнулял бы.
# Когда почините ключ Яндекса — верните True, и фильтр "хотя бы один контакт"
# снова заработает как задумано.
REQUIRE_CONTACT = False

# При большом количестве найденных компаний (сейчас это реально сотня+,
# спасибо OSM) слать по одному сообщению на компанию — плохая идея: Telegram
# не успевает обрабатывать десятки запросов подряд без пауз и рвёт соединение
# по таймауту, а дальше отправка просто падает и часть результатов теряется.
# Группируем по несколько штук в одно сообщение.
PLACES_PER_MESSAGE = 8

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

NICHE, CITY = range(2)


async def _send_with_retry(message, text: str, retries: int = 2) -> None:
    """reply_text с повторной попыткой при временных сбоях сети/таймаутах Telegram."""
    for attempt in range(retries + 1):
        try:
            await message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
            return
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
        except TimedOut:
            if attempt == retries:
                logger.warning("Не удалось отправить сообщение после %d попыток, пропускаю", retries + 1)
                return
            await asyncio.sleep(1.5)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    contact_line = (
        "📞 Показываю только тех, у кого есть хотя бы контакт (телефон, реже — "
        "WhatsApp/Telegram, если это редко проставлено в OpenStreetMap) — без "
        "контакта карточку не пришлю. Из 2GIS почти ничего не пройдёт этот фильтр: "
        "у них телефон платный, поэтому основные контакты — из Яндекса и OSM.\n\n"
        if REQUIRE_CONTACT
        else "⚠️ Сейчас фильтр «только с контактами» временно отключён (чинится "
        "ключ Яндекс.Карт), поэтому в выдаче будут все найденные компании, "
        "у части из них телефона не будет.\n\n"
    )
    await update.message.reply_text(
        "Привет! Я ищу организации по нише и городу сразу в 2GIS, Яндекс.Картах "
        "и OpenStreetMap.\n\n"
        f"{contact_line}"
        "🔁 Компании, которые я вам уже показывал раньше, повторно не присылаю — "
        "это привязано именно к вашему аккаунту, у других пользователей своя история. "
        "Команда /reset стирает вашу историю, если захотите начать заново.\n\n"
        "Могу дополнительно отфильтровать только тех, у кого нет сайта — удобно, "
        "если ищете клиентов на создание сайта.\n\n"
        "Команда /find — начать поиск."
    )


async def find_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not GIS_API_KEY and not YANDEX_API_KEY:
        await update.message.reply_text(
            "Бот ещё не настроен: не задан ни GIS_API_KEY, ни YANDEX_API_KEY. "
            "Получите ключи на https://dev.2gis.com и https://yandex.ru/dev/maps/geosearch/ "
            "и добавьте в .env"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Какая ниша интересует? Например: кофейня, стоматология, автосервис."
    )
    return NICHE


async def niche_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["niche"] = update.message.text.strip()
    await update.message.reply_text("В каком городе искать?")
    return CITY


async def city_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["city"] = update.message.text.strip()
    context.user_data["user_id"] = update.effective_user.id

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Все компании", callback_data="filter_all"),
                InlineKeyboardButton("Только без сайта 🚫", callback_data="filter_nosite"),
            ]
        ]
    )
    await update.message.reply_text(
        "Показать все компании или только те, у кого нет сайта?", reply_markup=keyboard
    )
    return ConversationHandler.END


async def filter_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["has_site"] = False if query.data == "filter_nosite" else None
    await run_search(query.message, context)


async def run_search(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    niche = context.user_data.get("niche", "")
    city = context.user_data.get("city", "")
    has_site = context.user_data.get("has_site")
    user_id = context.user_data.get("user_id")

    filter_label = " без сайта" if has_site is False else ""
    contact_label = " с контактами" if REQUIRE_CONTACT else ""

    await message.reply_text("Ищу во всех доступных источниках, минутку…")

    places, total, warnings, before_dedup = search_new(
        niche,
        city,
        GIS_API_KEY,
        YANDEX_API_KEY,
        has_site=has_site,
        require_contact=REQUIRE_CONTACT,
        user_id=user_id,
    )

    for w in warnings:
        await message.reply_text(f"⚠️ {w}")

    if not places:
        if before_dedup > 0:
            await message.reply_text(
                f"Нашёл {before_dedup} подходящих{filter_label}{contact_label} по «{niche}, "
                f"{city}», но все они уже были показаны вам раньше. Попробуйте другой "
                f"город/нишу или /reset, чтобы начать историю заново."
            )
        else:
            await message.reply_text(
                f"По запросу «{niche}, {city}»{filter_label}{contact_label} ничего не нашлось."
            )
        return

    # Пишем в базу сразу же — search_new уже сам собрал всё доступное
    # (все страницы и оба варианта поиска у 2GIS), делить на порции незачем.
    if user_id is not None:
        storage.mark_seen(user_id, places, city)

    header = (
        f"Нашёл {len(places)} новых организаций{contact_label}{filter_label} "
        f"по запросу «{niche}, {city}» (кандидатов найдено ~{total} с учётом всех источников):\n"
    )
    await message.reply_text(header)

    separator = "\n\n➖➖➖\n\n"
    for i in range(0, len(places), PLACES_PER_MESSAGE):
        chunk = places[i : i + PLACES_PER_MESSAGE]
        chunk_text = separator.join(p.to_text() for p in chunk)
        await _send_with_retry(message, chunk_text)
        await asyncio.sleep(0.3)  # небольшая пауза, чтобы не долбить Telegram без остановки


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ок, отменил.")
    return ConversationHandler.END


async def reset_seen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    count = storage.reset_user(user_id)
    await update.message.reply_text(
        f"Готово, стёр историю показанных компаний ({count} шт.). "
        "Теперь поиск снова покажет всех, включая тех, кого уже присылал раньше."
    )


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "Не задан TELEGRAM_BOT_TOKEN. Получите токен у @BotFather и добавьте в .env"
        )

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("find", find_start)],
        states={
            NICHE: [MessageHandler(filters.TEXT & ~filters.COMMAND, niche_received)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, city_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset_seen))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(filter_chosen, pattern="^filter_(all|nosite)$"))

    logger.info("Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()
