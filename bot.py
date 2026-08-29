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

# Раз в 2GIS всё равно упираемся в потолок 5 страниц * 10 = 50 компаний за
# поиск, пишем в базу не после каждой страницы, а одним пакетом раз в 50
# показанных (или когда достигли последней страницы, если 50 не набралось).
SESSION_BATCH_SIZE = 50

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

NICHE, CITY = range(2)


def _flush_session(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Пишет в БД всё, что накопилось в сессии с последнего сброса, и очищает буфер."""
    pending = context.user_data.get("session_shown")
    if pending:
        storage.mark_seen(user_id, pending)
    context.user_data["session_shown"] = []


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    contact_line = (
        "📞 Показываю только тех, у кого есть хотя бы телефон — без контакта "
        "карточку не пришлю. Из 2GIS почти ничего не пройдёт этот фильтр: у них "
        "телефон платный, поэтому реальные контакты в основном из Яндекса.\n\n"
        if REQUIRE_CONTACT
        else "⚠️ Сейчас фильтр «только с контактами» временно отключён (чинится "
        "ключ Яндекс.Карт), поэтому в выдаче будут все найденные компании, "
        "у части из них телефона не будет.\n\n"
    )
    await update.message.reply_text(
        "Привет! Я ищу организации по нише и городу сразу в 2GIS и Яндекс.Картах.\n\n"
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
    user_id = update.effective_user.id

    # Если с прошлого поиска в буфере что-то осталось не сброшенным в базу
    # (пользователь не дошёл до 50 и не долистал до последней страницы) —
    # сохраняем это сейчас, перед тем как начать новый поиск с чистого листа.
    _flush_session(context, user_id)

    context.user_data["city"] = update.message.text.strip()
    context.user_data["page"] = 1
    context.user_data["user_id"] = user_id

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
    start_page = context.user_data.get("page", 1)
    has_site = context.user_data.get("has_site")
    user_id = context.user_data.get("user_id")

    filter_label = " без сайта" if has_site is False else ""
    contact_label = " с контактами" if REQUIRE_CONTACT else ""

    if start_page > MAX_PAGE:
        await message.reply_text(
            f"Уже прошёлся по всем {MAX_PAGE} страницам по «{niche}, {city}»"
            f"{filter_label}{contact_label} — новых компаний тут больше нет. "
            f"Попробуйте другой город/нишу или /reset."
        )
        return

    places: list = []
    total = 0
    warnings: list[str] = []
    any_content_ever = False  # были ли вообще результаты хоть на одной просмотренной странице
    used_page = start_page

    # Пролистываем страницы 1..MAX_PAGE сами, пока не найдём непоказанные
    # компании — пользователю не нужно вручную жать "Показать ещё" ради
    # страницы, которая целиком уже была показана раньше.
    for p in range(start_page, MAX_PAGE + 1):
        places, total, warnings, before_dedup = search_all(
            niche,
            city,
            GIS_API_KEY,
            YANDEX_API_KEY,
            page=p,
            has_site=has_site,
            require_contact=REQUIRE_CONTACT,
            user_id=user_id,
        )
        used_page = p
        if before_dedup > 0:
            any_content_ever = True
        if places:
            break  # нашли непоказанные — дальше не листаем

    for w in warnings:
        await message.reply_text(f"⚠️ {w}")

    # Копим показанное в буфере сессии и пишем в базу одним пакетом, когда
    # накопилось >= SESSION_BATCH_SIZE или когда это последняя доступная
    # страница (дальше идти всё равно некуда, значит сессия завершена).
    session_shown = context.user_data.setdefault("session_shown", [])
    session_shown.extend(places)
    if user_id is not None and (len(session_shown) >= SESSION_BATCH_SIZE or used_page >= MAX_PAGE):
        _flush_session(context, user_id)

    if not places:
        if any_content_ever:
            await message.reply_text(
                f"Прошёлся по всем страницам (до {MAX_PAGE}) по «{niche}, {city}»"
                f"{filter_label}{contact_label} — новых компаний нет, все уже были "
                f"показаны раньше. Попробуйте другой город/нишу или /reset, "
                f"чтобы начать историю заново."
            )
        else:
            await message.reply_text(
                f"По запросу «{niche}, {city}»{filter_label}{contact_label} ничего не нашлось."
            )
        context.user_data["page"] = used_page + 1
        return

    header = (
        f"Нашёл {len(places)} новых организаций{contact_label}{filter_label} "
        f"по запросу «{niche}, {city}» (страница {used_page}, всего в источниках ~{total}):\n"
    )
    await message.reply_text(header)

    for place in places:
        await message.reply_text(place.to_text(), parse_mode="HTML", disable_web_page_preview=True)

    context.user_data["page"] = used_page + 1

    buttons = []
    if used_page < MAX_PAGE:
        buttons.append(InlineKeyboardButton("Показать ещё ➡️", callback_data="more"))
    if buttons:
        await message.reply_text(
            "Показываю максимум 5 страниц по 10 организаций из каждого источника.",
            reply_markup=InlineKeyboardMarkup([buttons]),
        )


async def more_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    # Страницу дальше двигает сам send_results (context.user_data["page"] уже
    # указывает на следующую непройденную страницу) — здесь просто вызываем его.
    await send_results(query.message, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ок, отменил.")
    return ConversationHandler.END


async def reset_seen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    context.user_data["session_shown"] = []  # иначе несброшенный буфер потом перезапишет очищенную историю
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
    application.add_handler(CallbackQueryHandler(more_results, pattern="^more$"))

    logger.info("Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()
