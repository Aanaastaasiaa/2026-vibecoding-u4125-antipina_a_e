#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот для сбора обратной связи (оценки 1–5 по трём аспектам).
Использует python-telegram-bot (async API) и SQLite.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import logging
import os
import random
import sqlite3
import threading
from datetime import datetime, time as dt_time, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request
import requests
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Настройка опроса: измените тексты ниже, чтобы задать свои вопросы.
# Количество аспектов должно оставаться равным трём — под колонки БД aspect1–3.
# ---------------------------------------------------------------------------
SURVEY_ASPECTS: list[str] = [
    "качество обслуживания",
    "скорость ответа",
    "удобство использования",
]

# Состояния диалога опроса (по одному на каждый вопрос)
(
    ASK_ASPECT1,
    ASK_ASPECT2,
    ASK_ASPECT3,
) = range(3)

# Клавиатура с оценками 1–5
RATING_KEYBOARD = ReplyKeyboardMarkup(
    [["1", "2", "3", "4", "5"]],
    one_time_keyboard=True,
    resize_keyboard=True,
)

# Путь к файлу БД рядом со скриптом
DB_PATH = Path(__file__).resolve().parent / "feedback.db"

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lstrip("@")
CAT_API_KEY = os.getenv("CAT_API_KEY", "").strip()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CAT_BREEDS_URL = "https://api.thecatapi.com/v1/breeds"
CAT_IMAGES_URL = "https://api.thecatapi.com/v1/images/search"
TRANSLATE_API_URL = "https://translate.googleapis.com/translate_a/single"
HTTP_TIMEOUT_SECONDS = 10

# Резервные описания пород используются, если API временно недоступно.
FALLBACK_BREED_INFO: list[str] = [
    "Британская короткошерстная: спокойная и дружелюбная порода, родом из Великобритании.",
    "Мейн-кун: крупная, общительная порода из США, известная мягким характером.",
    "Сиамская: активная и разговорчивая порода из Таиланда с яркой внешностью.",
    "Русская голубая: элегантная, тихая порода из России с густой серебристой шерстью.",
    "Рэгдолл: ласковая домашняя порода из США, хорошо ладит с семьей.",
    "Бенгальская: энергичная порода с выразительным окрасом, любит игры и внимание.",
]

# Резервная картинка, если API изображений временно недоступно.
FALLBACK_CAT_IMAGE_URL = "https://cdn2.thecatapi.com/images/MTY3ODIyMQ.jpg"

TRANSLATION_CACHE: dict[str, str] = {}

# Используем отдельную сессию и отключаем proxy-переменные окружения.
# Это помогает избежать ложных "сетевых" ошибок в окружениях с жёстким proxy.
CAT_API_SESSION = requests.Session()
CAT_API_SESSION.trust_env = False

app = Flask(__name__)


def cat_api_headers() -> dict[str, str]:
    """
    Возвращает заголовки для The Cat API.
    Если ключ не задан, отправляем запрос без авторизации.
    """
    if CAT_API_KEY:
        return {"x-api-key": CAT_API_KEY}
    return {}


def auto_translate_to_russian(text: str) -> str:
    """
    Автоматически переводит текст на русский через публичный endpoint Google Translate.
    При любой ошибке возвращает исходный текст.
    """
    source = (text or "").strip()
    if not source:
        return source
    if source in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[source]

    try:
        response = CAT_API_SESSION.get(
            TRANSLATE_API_URL,
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "ru",
                "dt": "t",
                "q": source,
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if (
            isinstance(payload, list)
            and payload
            and isinstance(payload[0], list)
            and payload[0]
        ):
            translated_parts: list[str] = []
            for item in payload[0]:
                if isinstance(item, list) and item:
                    translated_parts.append(str(item[0]))
            translated = "".join(translated_parts).strip()
            if translated:
                TRANSLATION_CACHE[source] = translated
                return translated
    except Exception:
        logger.exception("Не удалось выполнить автоперевод текста.")

    TRANSLATION_CACHE[source] = source
    return source


def get_connection() -> sqlite3.Connection:
    """Возвращает соединение с SQLite (row_factory для удобного доступа к строкам)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Создаёт таблицу feedback, если её ещё нет."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                aspect1 INTEGER NOT NULL,
                aspect2 INTEGER NOT NULL,
                aspect3 INTEGER NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_feedback_row(
    user_id: int,
    username: str | None,
    aspect1: int,
    aspect2: int,
    aspect3: int,
) -> None:
    """Сохраняет одну завершённую анкету в БД."""
    ts = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO feedback (user_id, username, aspect1, aspect2, aspect3, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, aspect1, aspect2, aspect3, ts),
        )
        conn.commit()


def fetch_report_stats() -> tuple[list[float], int] | None:
    """
    Возвращает (средние по aspect1–3, количество строк) или None, если данных нет.
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS cnt,
                AVG(aspect1) AS a1,
                AVG(aspect2) AS a2,
                AVG(aspect3) AS a3
            FROM feedback
            """
        ).fetchone()
    if row is None or row["cnt"] == 0:
        return None
    cnt = int(row["cnt"])
    avgs = [float(row["a1"]), float(row["a2"]), float(row["a3"])]
    return avgs, cnt


def is_admin_user(user: object | None) -> bool:
    """Проверяет, совпадает ли username пользователя с ADMIN_USERNAME из .env."""
    if not ADMIN_USERNAME or user is None:
        return False
    un = getattr(user, "username", None)
    if not un:
        return False
    return un.lower() == ADMIN_USERNAME.lower()


def display_user_name(update: Update) -> str:
    """Имя для уведомлений: полное имя из профиля или @username / id."""
    u = update.effective_user
    if u is None:
        return "неизвестный пользователь"
    parts = [p for p in (u.first_name, u.last_name) if p]
    if parts:
        return " ".join(parts)
    if u.username:
        return f"@{u.username}"
    return str(u.id)


async def notify_admin_new_answer(context: ContextTypes.DEFAULT_TYPE, user_label: str) -> None:
    """Шлёт руководителю уведомление о новом ответе."""
    if not ADMIN_USERNAME:
        logger.warning("ADMIN_USERNAME не задан — уведомление не отправлено.")
        return
    text = f"Получен новый ответ от {user_label}"
    chat_id = f"@{ADMIN_USERNAME}"
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except TelegramError as e:
        logger.error("Не удалось отправить уведомление руководителю: %s", e)


def question_text(aspect_index: int) -> str:
    """Текст вопроса по индексу аспекта (0..2)."""
    aspect = SURVEY_ASPECTS[aspect_index]
    return (
        f"Как вы оцениваете {aspect} по шкале от 1 (плохо) до 5 (отлично)?"
    )


def parse_rating(text: str) -> int | None:
    """Парсит оценку 1–5 или возвращает None при некорректном вводе."""
    t = (text or "").strip()
    if t not in {"1", "2", "3", "4", "5"}:
        return None
    return int(t)


def get_cat_breed_info() -> str:
    """
    Получает случайную информацию о породе через The Cat API.
    Ожидаемый формат: список объектов породы от /v1/breeds.
    """
    logger.info("Запрос списка пород: %s", CAT_BREEDS_URL)
    response = CAT_API_SESSION.get(
        CAT_BREEDS_URL,
        headers=cat_api_headers(),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload:
        raise ValueError("The Cat API вернул пустой список пород.")

    breed = random.choice(payload)
    if not isinstance(breed, dict):
        raise ValueError("Некорректный формат элемента породы.")

    name = str(breed.get("name", "")).strip() or "Неизвестная порода"
    origin_raw = str(breed.get("origin", "")).strip() or "unknown"
    temperament_raw = str(breed.get("temperament", "")).strip() or "no data"
    description_raw = str(breed.get("description", "")).strip() or "Description unavailable."
    origin = auto_translate_to_russian(origin_raw)
    temperament = auto_translate_to_russian(temperament_raw)
    description = auto_translate_to_russian(description_raw)
    life_span = str(breed.get("life_span", "")).strip() or "нет данных"
    intelligence = int(breed.get("intelligence", 0) or 0)
    energy_level = int(breed.get("energy_level", 0) or 0)
    child_friendly = int(breed.get("child_friendly", 0) or 0)
    dog_friendly = int(breed.get("dog_friendly", 0) or 0)
    hypoallergenic = bool(breed.get("hypoallergenic", 0))
    hypoallergenic_text = "да" if hypoallergenic else "нет"
    return (
        f"Порода: {name}\n"
        f"Страна происхождения: {origin}\n"
        f"Темперамент: {temperament}\n"
        f"Продолжительность жизни: {life_span} лет\n"
        f"Интеллект: {intelligence}/5\n"
        f"Энергичность: {energy_level}/5\n"
        f"Дружелюбна к детям: {child_friendly}/5\n"
        f"Дружелюбна к собакам: {dog_friendly}/5\n"
        f"Гипоаллергенная: {hypoallergenic_text}\n"
        f"Описание: {description}"
    )


def get_cat_image_url() -> str:
    """
    Получает URL случайного изображения через The Cat API.
    Ожидаемый формат: [{"url": "..."}].
    """
    logger.info("Запрос изображения кота: %s", CAT_IMAGES_URL)
    response = CAT_API_SESSION.get(
        CAT_IMAGES_URL,
        headers=cat_api_headers(),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, list) or not payload:
        raise ValueError("The Cat API вернул пустой список изображений.")
    image_url = str(payload[0].get("url", "")).strip()
    if not image_url:
        raise ValueError("Поле url отсутствует в ответе The Cat API.")
    return image_url


async def send_cat_fact_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """
    Пытается отправить информацию о породе в текущий чат.
    Возвращает True, если сообщение отправлено успешно.
    """
    if update.message is None:
        return False
    try:
        breed_info = await asyncio.to_thread(get_cat_breed_info)
        prefix = "Случайная информация о породе котов:"
    except requests.Timeout:
        logger.exception("Тайм-аут при запросе информации о породах.")
        breed_info = random.choice(FALLBACK_BREED_INFO)
        prefix = "Информация о породах временно недоступна (тайм-аут). Резерв:"
    except requests.HTTPError:
        logger.exception("HTTP-ошибка The Cat API при запросе пород.")
        breed_info = random.choice(FALLBACK_BREED_INFO)
        prefix = "Информация о породах временно недоступна (ошибка API). Резерв:"
    except requests.RequestException:
        logger.exception("Сетевая ошибка при запросе информации о породах.")
        breed_info = random.choice(FALLBACK_BREED_INFO)
        prefix = "Информация о породах временно недоступна (ошибка сети). Резерв:"
    except ValueError:
        logger.exception("Пустой/некорректный ответ The Cat API для пород.")
        breed_info = random.choice(FALLBACK_BREED_INFO)
        prefix = "Информация о породах временно недоступна. Резерв:"
    except Exception:
        logger.exception("Неожиданная ошибка при получении данных о породах.")
        breed_info = random.choice(FALLBACK_BREED_INFO)
        prefix = "Информация о породах временно недоступна. Резерв:"

    await update.message.reply_text(f"{prefix}\n{breed_info}")
    return True


# --- Команды вне опроса -----------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветствие и краткая навигация."""
    await update.message.reply_text(
        "Добро пожаловать! Я бот для сбора обратной связи. "
        "Используйте /feedback, чтобы пройти опрос, /report для отчёта, "
        "/catfact для информации о породе и /catimage для фото кота."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Справка по командам."""
    text = (
        "Доступные команды:\n"
        "/start — приветствие и подсказка\n"
        "/feedback — пройти опрос (3 вопроса, оценка от 1 до 5)\n"
        "/report — сводный отчёт (только для руководителя)\n"
        "/catfact — случайная информация о породе кота\n"
        "/catimage — случайное изображение кота\n"
        "/dailycat — ежедневная информация о породе и фото кота в 10:00\n"
        "/cancel — прервать опрос\n"
        "/help — эта справка\n\n"
        "После каждого завершённого опроса руководитель получает уведомление."
    )
    await update.message.reply_text(text)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сводный отчёт по БД (только для пользователя с ADMIN_USERNAME)."""
    if not is_admin_user(update.effective_user):
        await update.message.reply_text(
            "У вас нет прав на просмотр отчёта."
        )
        return
    stats = fetch_report_stats()
    if stats is None:
        await update.message.reply_text(
            "Пока нет ни одного ответа — сформировать отчёт нельзя."
        )
        return
    avgs, n = stats
    # Формат отчёта по заданию
    parts = [
        f"{SURVEY_ASPECTS[i]}: средняя оценка — {avgs[i]:.2f}"
        for i in range(3)
    ]
    body = ", ".join(parts)
    msg = f"Сводный отчёт: {body}. Всего ответов: {n}"
    await update.message.reply_text(msg)


async def cmd_catfact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет случайную информацию о породе котов."""
    await send_cat_fact_message(update, context)


async def cmd_catimage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет случайное изображение кота."""
    if update.message is None:
        return

    image_url = FALLBACK_CAT_IMAGE_URL
    fallback_reason = ""
    try:
        image_url = await asyncio.to_thread(get_cat_image_url)
    except requests.Timeout:
        logger.exception("Тайм-аут при запросе изображения кота.")
        fallback_reason = "Не удалось получить изображение (тайм-аут). Отправляю резервную картинку."
    except requests.HTTPError:
        logger.exception("HTTP-ошибка The Cat API при запросе изображения.")
        fallback_reason = "Не удалось получить изображение (ошибка API). Отправляю резервную картинку."
    except requests.RequestException:
        logger.exception("Сетевая ошибка при запросе изображения кота.")
        fallback_reason = "Не удалось получить изображение (ошибка сети). Отправляю резервную картинку."
    except ValueError:
        logger.exception("Пустой/некорректный ответ The Cat API для изображения.")
        fallback_reason = "Не удалось получить изображение. Отправляю резервную картинку."
    except Exception:
        logger.exception("Неожиданная ошибка при получении изображения кота.")
        fallback_reason = "Не удалось получить изображение. Отправляю резервную картинку."

    if fallback_reason:
        await update.message.reply_text(fallback_reason)

    try:
        await update.message.reply_photo(photo=image_url, caption="Мяу! 😻")
    except TelegramError as e:
        logger.error("Не удалось отправить изображение кота: %s", e)
        if image_url != FALLBACK_CAT_IMAGE_URL:
            try:
                await update.message.reply_photo(
                    photo=FALLBACK_CAT_IMAGE_URL, caption="Мяу! 😻"
                )
                return
            except TelegramError:
                logger.exception("Не удалось отправить даже резервное изображение кота.")
        await update.message.reply_text("Не удалось загрузить картинку.")


async def send_daily_cat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Задача JobQueue: отправляет ежедневную информацию о породе и фото."""
    job = context.job
    if job is None or job.chat_id is None:
        return

    # Отдельно обрабатываем ошибки, чтобы ежедневная задача не прерывалась.
    try:
        breed_info = await asyncio.to_thread(get_cat_breed_info)
    except requests.Timeout:
        logger.exception("Тайм-аут при получении информации о породах для рассылки.")
        breed_info = random.choice(FALLBACK_BREED_INFO)
    except requests.RequestException:
        logger.exception("Сетевая ошибка при получении информации о породах для рассылки.")
        breed_info = random.choice(FALLBACK_BREED_INFO)
    except ValueError:
        logger.exception("Пустые/некорректные данные о породах в ежедневной рассылке.")
        breed_info = random.choice(FALLBACK_BREED_INFO)
    except Exception:
        logger.exception("Неожиданная ошибка в ежедневной задаче /dailycat.")
        breed_info = random.choice(FALLBACK_BREED_INFO)

    try:
        image_url = await asyncio.to_thread(get_cat_image_url)
    except (requests.Timeout, requests.RequestException, ValueError):
        logger.exception("Не удалось получить изображение для ежедневной рассылки.")
        image_url = FALLBACK_CAT_IMAGE_URL
    except Exception:
        logger.exception("Неожиданная ошибка изображения в /dailycat.")
        image_url = FALLBACK_CAT_IMAGE_URL

    caption = f"Ежедневная информация о породе:\n{breed_info}"
    try:
        await context.bot.send_photo(
            chat_id=job.chat_id,
            photo=image_url,
            caption=caption,
        )
    except TelegramError as e:
        logger.error("Не удалось отправить ежедневное сообщение о котах: %s", e)


async def cmd_dailycat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Включает ежедневное напоминание в 10:00 по локальному времени."""
    if update.message is None or update.effective_chat is None:
        return
    if context.job_queue is None:
        await update.message.reply_text(
            "Планировщик недоступен. Установите зависимость python-telegram-bot[job-queue]."
        )
        return

    chat_id = update.effective_chat.id
    job_name = f"dailycat:{chat_id}"

    for old_job in context.job_queue.get_jobs_by_name(job_name):
        old_job.schedule_removal()

    local_tz = datetime.now().astimezone().tzinfo
    send_time = dt_time(hour=10, minute=0, tzinfo=local_tz)
    context.job_queue.run_daily(
        send_daily_cat_job,
        time=send_time,
        name=job_name,
        chat_id=chat_id,
    )

    tz_name = datetime.now().astimezone().tzname() or "локальное время"
    await update.message.reply_text(
        f"Готово! Ежедневный факт и фото кота будут приходить в 10:00 ({tz_name})."
    )


# --- Опрос (ConversationHandler) --------------------------------------------


async def feedback_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало опроса: сброс буфера и первый вопрос."""
    context.user_data["ratings"] = []
    await update.message.reply_text(
        question_text(0),
        reply_markup=RATING_KEYBOARD,
    )
    return ASK_ASPECT1


async def receive_aspect1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ответа на первый вопрос."""
    val = parse_rating(update.message.text)
    if val is None:
        await update.message.reply_text(
            "Пожалуйста, выберите оценку от 1 до 5 (кнопкой или цифрой).",
            reply_markup=RATING_KEYBOARD,
        )
        return ASK_ASPECT1
    context.user_data.setdefault("ratings", []).append(val)
    await update.message.reply_text(
        question_text(1),
        reply_markup=RATING_KEYBOARD,
    )
    return ASK_ASPECT2


async def receive_aspect2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ответа на второй вопрос."""
    val = parse_rating(update.message.text)
    if val is None:
        await update.message.reply_text(
            "Пожалуйста, выберите оценку от 1 до 5 (кнопкой или цифрой).",
            reply_markup=RATING_KEYBOARD,
        )
        return ASK_ASPECT2
    context.user_data.setdefault("ratings", []).append(val)
    await update.message.reply_text(
        question_text(2),
        reply_markup=RATING_KEYBOARD,
    )
    return ASK_ASPECT3


async def receive_aspect3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Третий ответ: сохранение в БД, уведомление руководителю, завершение."""
    val = parse_rating(update.message.text)
    if val is None:
        await update.message.reply_text(
            "Пожалуйста, выберите оценку от 1 до 5 (кнопкой или цифрой).",
            reply_markup=RATING_KEYBOARD,
        )
        return ASK_ASPECT3
    ratings = context.user_data.setdefault("ratings", [])
    ratings.append(val)
    if len(ratings) != 3:
        # Защита от несогласованного состояния
        await update.message.reply_text(
            "Произошла ошибка сессии. Начните опрос снова: /feedback",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    user = update.effective_user
    uid = user.id if user else 0
    uname = user.username if user else None
    a1, a2, a3 = ratings[0], ratings[1], ratings[2]

    try:
        save_feedback_row(uid, uname, a1, a2, a3)
    except sqlite3.Error as e:
        logger.exception("Ошибка SQLite при сохранении: %s", e)
        await update.message.reply_text(
            "Не удалось сохранить ответы. Попробуйте позже.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.pop("ratings", None)
        return ConversationHandler.END

    await notify_admin_new_answer(context, display_user_name(update))
    await update.message.reply_text(
        "Спасибо! Ваши ответы сохранены.",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.pop("ratings", None)
    return ConversationHandler.END


async def feedback_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Прерывание опроса по /cancel."""
    context.user_data.pop("ratings", None)
    await update.message.reply_text(
        "Опрос отменён. Вы можете начать снова командой /feedback.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def _conversation_state_from_progress(answered_count: int) -> int:
    """
    Восстанавливает состояние опроса по числу уже сохранённых ответов
    (0 — ждём первый ответ, 1 — второй, 2 — третий).
    """
    if answered_count <= 0:
        return ASK_ASPECT1
    if answered_count == 1:
        return ASK_ASPECT2
    return ASK_ASPECT3


async def wrong_input_1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Пожалуйста, отправьте только цифру от 1 до 5 или нажмите кнопку. "
        "Для отмены — /cancel.",
        reply_markup=RATING_KEYBOARD,
    )
    return ASK_ASPECT1


async def wrong_input_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Пожалуйста, отправьте только цифру от 1 до 5 или нажмите кнопку. "
        "Для отмены — /cancel.",
        reply_markup=RATING_KEYBOARD,
    )
    return ASK_ASPECT2


async def wrong_input_3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Пожалуйста, отправьте только цифру от 1 до 5 или нажмите кнопку. "
        "Для отмены — /cancel.",
        reply_markup=RATING_KEYBOARD,
    )
    return ASK_ASPECT3


async def report_from_conversation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    /report во время опроса: для руководителя — отчёт и выход из диалога;
    для остальных — отказ и возврат к текущему вопросу.
    """
    ratings = context.user_data.get("ratings", [])
    progress = len(ratings)
    if not is_admin_user(update.effective_user):
        await update.message.reply_text("У вас нет прав на просмотр отчёта.")
        return _conversation_state_from_progress(progress)

    await cmd_report(update, context)
    context.user_data.pop("ratings", None)
    await update.message.reply_text(
        "Опрос прерван из-за запроса отчёта.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def help_from_conversation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Показ /help без сброса опроса; возврат к тому же шагу."""
    await cmd_help(update, context)
    ratings = context.user_data.get("ratings", [])
    return _conversation_state_from_progress(len(ratings))


async def start_from_conversation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Повторное приветствие по /start во время опроса без потери прогресса."""
    await cmd_start(update, context)
    ratings = context.user_data.get("ratings", [])
    return _conversation_state_from_progress(len(ratings))


def build_conversation_handler() -> ConversationHandler:
    """Собирает ConversationHandler для сценария /feedback."""
    return ConversationHandler(
        entry_points=[CommandHandler("feedback", feedback_entry)],
        states={
            ASK_ASPECT1: [
                MessageHandler(filters.Regex("^[1-5]$"), receive_aspect1),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    wrong_input_1,
                ),
            ],
            ASK_ASPECT2: [
                MessageHandler(filters.Regex("^[1-5]$"), receive_aspect2),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    wrong_input_2,
                ),
            ],
            ASK_ASPECT3: [
                MessageHandler(filters.Regex("^[1-5]$"), receive_aspect3),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    wrong_input_3,
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", feedback_cancel),
            CommandHandler("report", report_from_conversation),
            CommandHandler("help", help_from_conversation),
            CommandHandler("start", start_from_conversation),
        ],
        name="feedback_survey",
        allow_reentry=True,
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок PTB."""
    logger.exception("Ошибка при обработке update: %s", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Произошла внутренняя ошибка. Попробуйте позже или используйте /help."
            )
        except TelegramError:
            pass


def register_handlers(application: Application) -> None:
    """Регистрирует все handlers бота."""
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("report", cmd_report))
    application.add_handler(CommandHandler("catfact", cmd_catfact))
    application.add_handler(CommandHandler("catimage", cmd_catimage))
    application.add_handler(CommandHandler("dailycat", cmd_dailycat))
    application.add_handler(build_conversation_handler())
    application.add_error_handler(on_error)


telegram_application = (
    Application.builder()
    .token(BOT_TOKEN)
    .concurrent_updates(False)
    .build()
)
telegram_loop: asyncio.AbstractEventLoop | None = None


def _telegram_loop_worker() -> None:
    """Запускает PTB-приложение в отдельном event loop."""
    global telegram_loop
    telegram_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telegram_loop)
    telegram_loop.run_until_complete(telegram_application.initialize())
    telegram_loop.run_until_complete(telegram_application.start())
    logger.info("Telegram application запущено в webhook-режиме.")
    telegram_loop.run_forever()


@app.route("/webhook", methods=["POST"])
def webhook() -> tuple[str, int]:
    """Принимает update от Telegram и передаёт его в PTB Application."""
    update_data = request.get_json(silent=True)
    if not isinstance(update_data, dict):
        return "bad request", 400

    if telegram_loop is None:
        logger.error("Telegram loop ещё не инициализирован.")
        return "service unavailable", 503

    update = Update.de_json(update_data, telegram_application.bot)
    if update is None:
        return "ok", 200

    try:
        future = asyncio.run_coroutine_threadsafe(
            telegram_application.process_update(update),
            telegram_loop,
        )
        future.result(timeout=HTTP_TIMEOUT_SECONDS + 5)
    except FutureTimeoutError:
        logger.exception("Тайм-аут обработки webhook update.")
        return "timeout", 504
    except Exception:
        logger.exception("Ошибка обработки webhook update.")
        return "error", 500

    return "ok", 200


@app.route("/", methods=["GET"])
def home() -> str:
    """Техническая страница статуса."""
    return "Cat Bot is running!"


def main() -> None:
    """Точка входа: инициализация бота и запуск Flask webhook-сервера."""
    if not BOT_TOKEN:
        raise SystemExit(
            "Не задан BOT_TOKEN. Создайте файл .env по образцу .env.example."
        )
    init_db()
    register_handlers(telegram_application)

    worker = threading.Thread(
        target=_telegram_loop_worker,
        name="telegram-webhook-loop",
        daemon=True,
    )
    worker.start()

    port = int(os.environ.get("PORT", "5000"))
    logger.info("Flask webhook-сервер запущен на порту %s.", port)
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
