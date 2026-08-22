"""
Telegram-бот для поиска организаций по нише и городу через официальные API
2GIS и Яндекс.Карт одновременно.

Особенности:
  - 2GIS: бесплатный demo-ключ, но телефон/WhatsApp/Instagram — только платно,
    поэтому у карточек из 2GIS телефона нет.
  - Яндекс.Карты: бесплатный API «Поиск по организациям» (до 500 запросов/сутки),
    телефон отдаёт бесплатно — используем именно его для требования "с контактами".
  - Обязательный фильтр: показываем только организации хотя бы с одним контактом
    (на практике — с телефоном; Telegram/WhatsApp как отдельные поля бесплатные
    API не отдают). Из-за этого карточки из 2GIS в выдаче почти не встретятся —
    это не баг, а следствие того, что 2GIS прячет контакты за платной подпиской.

Запуск:
  1. pip install -r requirements.txt
  2. создать .env (см. .env.example) с TELEGRAM_BOT_TOKEN, GIS_API_KEY, YANDEX_API_KEY
  3. python bot.py
"""

import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from aggregator import search_all
from gis_client import MAX_PAGE

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GIS_API_KEY = os.environ.get("GIS_API_KEY", "")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

NICHE, CITY = range(2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я ищу организации по нише и городу сразу в 2GIS и Яндекс.Картах.\n\n"
        "📞 Показываю только тех, у кого есть хотя бы телефон — без контакта "
        "карточку не пришлю. Из 2GIS почти ничего не пройдёт этот фильтр: у них "
        "телефон платный, поэтому реальные контакты в основном из Яндекса.\n\n"
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
    context.user_data["page"] = 1

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
    await send_results(query.message, context)


async def send_results(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    niche = context.user_data.get("niche", "")
    city = context.user_data.get("city", "")
    page = context.user_data.get("page", 1)
    has_site = context.user_data.get("has_site")

    places, total, warnings = search_all(
        niche,
        city,
        GIS_API_KEY,
        YANDEX_API_KEY,
        page=page,
        has_site=has_site,
        require_contact=True,
    )

    for w in warnings:
        await message.reply_text(f"⚠️ {w}")

    filter_label = " без сайта" if has_site is False else ""

    if not places:
        await message.reply_text(
            f"По запросу «{niche}, {city}»{filter_label} с контактами ничего не нашлось "
            f"(страница {page})."
        )
        return

    header = (
        f"Нашёл {len(places)} организаций с контактами{filter_label} "
        f"по запросу «{niche}, {city}» (страница {page}, всего в источниках ~{total}):\n"
    )
    await message.reply_text(header)

    for place in places:
        await message.reply_text(place.to_text(), parse_mode="HTML", disable_web_page_preview=True)

    buttons = []
    if page < MAX_PAGE:
        buttons.append(InlineKeyboardButton("Показать ещё ➡️", callback_data="more"))
    if buttons:
        await message.reply_text(
            "Показываю максимум 5 страниц по 10 организаций из каждого источника.",
            reply_markup=InlineKeyboardMarkup([buttons]),
        )


async def more_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["page"] = context.user_data.get("page", 1) + 1
    await send_results(query.message, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ок, отменил.")
    return ConversationHandler.END


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
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(filter_chosen, pattern="^filter_(all|nosite)$"))
    application.add_handler(CallbackQueryHandler(more_results, pattern="^more$"))

    logger.info("Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()
