"""
Telegram-бот для поиска организаций по нише и городу через официальный 2GIS API.

Аналог GisFind, но:
  - бесплатный (использует бесплатный demo-ключ 2GIS)
  - без телефонов / WhatsApp / Instagram — эти данные 2GIS отдаёт только по
    платной подписке, на demo-ключе их просто нет. Бот честно даёт то, что
    доступно бесплатно: название, категория, адрес, рейтинг.

Запуск:
  1. pip install -r requirements.txt
  2. создать .env (см. .env.example) с TELEGRAM_BOT_TOKEN и GIS_API_KEY
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

from gis_client import GisApiError, MAX_PAGE, search_places

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GIS_API_KEY = os.environ.get("GIS_API_KEY", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

NICHE, CITY = range(2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я ищу организации по нише и городу через открытые данные 2GIS.\n\n"
        "⚠️ Важно: телефон, WhatsApp и Instagram я не даю — 2GIS отдаёт контакты "
        "только по платной подписке. Показываю название, категорию, адрес и рейтинг.\n\n"
        "Команда /find — начать поиск."
    )


async def find_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not GIS_API_KEY:
        await update.message.reply_text(
            "Бот ещё не настроен: не задан GIS_API_KEY. Получите демо-ключ на "
            "https://dev.2gis.com и добавьте его в .env"
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
    await send_results(update.message, context)
    return ConversationHandler.END


async def send_results(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    niche = context.user_data.get("niche", "")
    city = context.user_data.get("city", "")
    page = context.user_data.get("page", 1)

    try:
        places, total = search_places(niche, city, GIS_API_KEY, page=page)
    except GisApiError as e:
        await message.reply_text(f"Не получилось получить данные: {e}")
        return

    if not places:
        await message.reply_text(
            f"По запросу «{niche}, {city}» ничего не нашлось (страница {page})."
        )
        return

    header = f"Нашёл {total} организаций по запросу «{niche}, {city}» (страница {page}):\n"
    await message.reply_text(header)

    for place in places:
        await message.reply_text(place.to_text(), parse_mode="HTML", disable_web_page_preview=True)

    buttons = []
    if page < MAX_PAGE:
        buttons.append(InlineKeyboardButton("Показать ещё ➡️", callback_data="more"))
    if buttons:
        await message.reply_text(
            "Демо-ключ 2GIS показывает максимум 5 страниц по 10 организаций.",
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
    application.add_handler(CallbackQueryHandler(more_results, pattern="^more$"))

    logger.info("Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()
