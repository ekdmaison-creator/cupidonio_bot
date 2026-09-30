import asyncio
import os
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openai import OpenAI
from aiohttp import web

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    raise ValueError("BOT_TOKEN или DEEPSEEK_API_KEY не найдены!")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

import database

PRICE = 300


# ============ FSM ============

class RegistrationForm(StatesGroup):
    name = State()
    user_gender = State()
    partner_name = State()
    partner_gender = State()
    meeting_date = State()
    meeting_place = State()
    hobbies = State()
    favorite_movie = State()
    love_language = State()
    relationship_state = State()


class TreasureForm(StatesGroup):
    rooms = State()


class FeedbackForm(StatesGroup):
    waiting_feedback = State()


# ============ КЛАВИАТУРЫ ============

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Задание"), KeyboardButton(text="✅ Выполнено")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🗺️ Карта сокровищ")],
        [KeyboardButton(text="💭 Состояние"), KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="💎 Подписка")],
    ],
    resize_keyboard=True
)

love_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💬 Слова"), KeyboardButton(text="⏰ Время")],
        [KeyboardButton(text="🎁 Подарки"), KeyboardButton(text="🤝 Помощь")],
        [KeyboardButton(text="🤗 Прикосновения")]
    ],
    resize_keyboard=True
)

gender_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👨 Мужчина"), KeyboardButton(text="👩 Женщина")]
    ],
    resize_keyboard=True
)

relationship_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💚 Всё отлично")],
        [KeyboardButton(text="💛 Небольшие трудности")],
        [KeyboardButton(text="🧡 Отдалились")],
        [KeyboardButton(text="❤️‍🩹 Кризис")],
    ],
    resize_keyboard=True
)

mood_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="😍 Всё прекрасно", callback_data="mood_great")],
    [InlineKeyboardButton(text="🙂 Хорошо", callback_data="mood_good")],
    [InlineKeyboardButton(text="😐 Есть трудности", callback_data="mood_hard")],
    [InlineKeyboardButton(text="😔 Сложный период", callback_data="mood_crisis")],
])

settings_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Каждый день")],
        [KeyboardButton(text="🗓 3 раза в неделю")],
        [KeyboardButton(text="⬅️ Назад в меню")]
    ],
    resize_keyboard=True
)


# ============ ХЕЛПЕРЫ ============

async def require_subscription(message: types.Message, user_id: int) -> bool:
    if not database.check_subscription(user_id):
        await message.answer(
            "🔒 <b>Доступ приостановлен</b>\n\n"
            "Ваш пробный период закончился, и теперь все задания доступны только по подписке.\n\n"
            f"💎 <b>Всего {PRICE} ₽ в месяц</b> — это меньше 10 рублей в день за крепкие отношения.\n\n"
            "Нажмите /subscribe, чтобы оформить подписку.",
            parse_mode="HTML"
        )
        return False
    return True


def clean_markdown(text: str) -> str:
    """Убирает markdown-символы и превращает их в читаемый текст."""
    import re
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'\1', text)
    text = re.sub(r'^-{3,}$', '─────────────', text, flags=re.MULTILINE)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def calculate_relationship_duration(meeting_date_str: str) -> dict:
    """
    Вычисляет длительность отношений из строки даты знакомства.
    Возвращает словарь с полями: days, months, years, stage, description.
    """
    import re
    try:
        # Пробуем разные форматы даты
        date_str = meeting_date_str.strip()
        parsed_date = None
        
        # Формат ДД.ММ.ГГГГ
        match = re.match(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', date_str)
        if match:
            day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            parsed_date = datetime(year, month, day)
        else:
            # Формат ДД/ММ/ГГГГ
            match = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_str)
            if match:
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                parsed_date = datetime(year, month, day)
        
        if not parsed_date:
            return {
                "days": 0, "months": 0, "years": 0,
                "stage": "неизвестно",
                "description": "длительность отношений не определена"
            }
        
        delta = datetime.now() - parsed_date
        days = delta.days
        years = days // 365
        months = (days % 365) // 30
        
        # Определяем стадию отношений
        if days < 30:
            stage = "совсем недавно вместе"
            description = "Вы только начали свой путь вместе — это конфетно-букетный период. Задания должны быть лёгкими, игривыми, с ноткой новизны и волнения. Помогите им лучше узнать друг друга."
        elif days < 180:
            stage = "в начале пути"
            description = "Вы вместе несколько месяцев — узнаёте друг друга по-настоящему. Задания могут быть чуть глубже, но всё ещё с лёгкостью и исследованием."
        elif days < 365:
            stage = "первый год вместе"
            description = "Вы приближаетесь к первой серьёзной отметке — году вместе. Задания должны помогать закреплять привычку быть внимательными друг к другу."
        elif years < 3:
            stage = "пара со стажем 1-3 года"
            description = "Вы вместе уже больше года — есть свои ритуалы и история. Задания могут быть более глубокими, с отсылками к общим воспоминаниям, с заботой о сохранении новизны."
        elif years < 7:
            stage = "пара со стажем 3-7 лет"
            description = "Вы вместе несколько лет. В отношениях может появляться рутина. Задания должны добавлять свежесть, новые форматы, неожиданные повороты. Важно не скатываться в банальности."
        elif years < 15:
            stage = "пара со стажем 7-15 лет"
            description = "Вы вместе долгие годы. У вас глубокие связи и общая история. Задания — про возвращение к истокам, про маленькие искры в привычной жизни, про благодарность друг другу."
        else:
            stage = "пара с многолетней историей"
            description = "Вы вместе больше 15 лет. Ваша связь — это нечто особенное. Задания — про нежность, заботу, про то, чтобы заново открывать друг друга. Никаких банальностей, только искреннее и тёплое."
        
        return {
            "days": days,
            "months": months,
            "years": years,
            "stage": stage,
            "description": description
        }
    except Exception as e:
        print(f"Ошибка вычисления длительности: {e}")
        return {
            "days": 0, "months": 0, "years": 0,
            "stage": "неизвестно",
            "description": "длительность отношений не определена"
        }

def parse_hobbies(hobbies_str: str) -> list:
    """
    Разбирает строку увлечений на список.
    Пример: 'путешествия, кино, кулинария' -> ['путешествия', 'кино', 'кулинария']
    """
    if not hobbies_str or hobbies_str.strip() == "":
        return []
    
    # Разделяем по запятой, точке с запятой или слэшу
    import re
    parts = re.split(r'[,;/]', hobbies_str)
    hobbies = [p.strip().lower() for p in parts if p.strip()]
    
    # Убираем дубликаты, сохраняя порядок
    seen = set()
    result = []
    for h in hobbies:
        if h not in seen and len(h) > 1:
            seen.add(h)
            result.append(h)
    
    return result


def get_hobby_hint(hobbies: list, relationship_state: str) -> str:
    """
    Возвращает инструкцию для AI о том, как использовать увлечения пары.
    """
    if not hobbies:
        return "\nУ пары не указаны конкретные увлечения — используй универсальные, но живые идеи.\n"
    
    hobbies_text = ", ".join(hobbies)
    
    hint = (
        f"\nУВЛЕЧЕНИЯ ПАРЫ (используй ИХ в задании — это критически важно!):\n"
        f"— {hobbies_text}\n\n"
        f"Как использовать увлечения:\n"
        f"— Если увлечение 'путешествия' — задание про планирование поездки, поиск новых мест, воспоминания о поездках, изучение стран.\n"
        f"— Если 'кино' или 'сериалы' — задание про совместный просмотр, обсуждение, игру по фильму, цитату из любимого.\n"
        f"— Если 'кулинария' — задание про совместное приготовление блюда, поиск нового рецепта, ужин с сюрпризом.\n"
        f"— Если 'спорт' или 'фитнес' — задание про совместную тренировку, прогулку, челлендж на активность.\n"
        f"— Если 'музыка' — задание про совместный плейлист, песню, концерт, танец под любимую мелодию.\n"
        f"— Если 'книги' или 'чтение' — задание про обмен книгами, обсуждение прочитанного, чтение вслух друг другу.\n"
        f"— Если 'игры' — задание про совместную игру, настолку, видеоигру, челлендж.\n"
        f"— Если 'творчество' — задание про совместное творчество, рисунок, поделку, идею.\n"
        f"— Если 'прогулки' или 'природа' — задание про совместную прогулку, пикник, наблюдение за закатом.\n"
        f"— Если увлечение не из списка — придумай, как связать задание с ним естественно и живо.\n"
    )
    
    # Дополнительная корректировка в зависимости от состояния отношений
    if relationship_state in ["отдалились", "кризис"]:
        hint += (
            f"\n⚠️ ВАЖНО: у пары сейчас непростое время. Задание через увлечения должно быть "
            f"МЯГКИМ и НЕОБЯЗЫВАЮЩИМ — просто совместное действие, а не 'романтический вечер'. "
            f"Никакого давления.\n"
        )
    
    return hint

def generate_task_from_ai(user_id, task_type="text", category=None):
    user = database.get_user(user_id)
    if not user:
        return None
    
    name = user[1]
    partner = user[2]
    place = user[4]
    hobbies = user[5]
    movie = user[6]
    love_lang = user[7]
    user_gender = user[10] if len(user) > 10 else "не указан"
    partner_gender = user[11] if len(user) > 11 else "не указан"
    relationship_state = user[12] if len(user) > 12 else "отлично"

    # Вычисляем длительность отношений
    meeting_date = user[3] if len(user) > 3 else ""
    duration_info = calculate_relationship_duration(meeting_date)
    duration_text = (
        f"\nДЛИТЕЛЬНОСТЬ ОТНОШЕНИЙ:\n"
        f"— Вместе: {duration_info['years']} г. {duration_info['months']} мес. ({duration_info['days']} дней)\n"
        f"— Стадия: {duration_info['stage']}\n"
        f"— Рекомендация по тону: {duration_info['description']}\n"
    )

    # Разбираем увлечения и формируем инструкцию
    hobbies_list = parse_hobbies(hobbies)
    hobby_hint = get_hobby_hint(hobbies_list, relationship_state)

    feedback_list = database.get_recent_feedback(user_id, limit=3)
    feedback_text = ""
    if feedback_list:
        feedback_text = "\n\nУЧТИ ЭТИ ПОЖЕЛАНИЯ ПОЛЬЗОВАТЕЛЯ (не повторяй прошлые ошибки):\n"
        for i, fb in enumerate(feedback_list, 1):
            feedback_text += f"{i}. {fb}\n"
    
    # Пол и роли — явно и однозначно
    if user_gender == "мужчина":
        user_role = f"мужчина по имени {name}"
        partner_role = f"его девушка/жена {partner}"
    elif user_gender == "женщина":
        user_role = f"женщина по имени {name}"
        partner_role = f"её парень/муж {partner}"
    else:
        user_role = f"человек по имени {name}"
        partner_role = f"партнёр {partner}"
    
    if partner_gender == "мужчина":
        partner_desc = f"мужчина {partner}"
    elif partner_gender == "женщина":
        partner_desc = f"женщина {partner}"
    else:
        partner_desc = f"партнёр {partner}"
    
    # Тон в зависимости от состояния отношений
    state_instructions = {
        "отлично": (
            "У пары всё хорошо. Задание должно добавить огня, свежести и глубины. "
            "Не бойся смелых идей, лёгкой интриги, новых форматов. Тон — тёплый, живой, игривый."
        ),
        "небольшие трудности": (
            "У пары небольшие трудности, но чувства живы. Задание должно вернуть тепло, "
            "напомнить, почему они вместе. Тон — мягкий, поддерживающий, без давления. "
            "Избегай серьёзных разговоров 'по душам' — только лёгкое возвращение тепла."
        ),
        "отдалились": (
            "Пара эмоционально отдалилась. Задание должно мягко восстановить связь, "
            "без обвинений и 'выяснений отношений'. Тон — аккуратный, ненавязчивый. "
            "Маленькие шаги: совместное действие, общее дело, тихий момент вдвоём."
        ),
        "кризис": (
            "У пары кризис. Задание — очень мягкое, без давления и романтики 'через силу'. "
            "Тон — заботливый, спокойный. Подойдут простые человеческие жесты: "
            "чашка чая, поддержка в мелочи, спокойное присутствие. Никаких сюрпризов и 'огня'."
        ),
    }
    state_hint = state_instructions.get(relationship_state, state_instructions["отлично"])
    
    if not category:
        import random
        categories = ["разговор", "сюрприз", "воспоминание", "близость", "игра", "приключение", "творчество", "забота"]
        category = random.choice(categories)
    
    category_instructions = {
        "разговор": "Задание про тёплый разговор. НЕ банальный вопрос 'как дела'. Конкретика: обмен мечтами на год, вопрос из прошлого, смешная история из детства.",
        "сюрприз": "Задание про маленький сюрприз для партнёра. Конкретика: то, что партнёр любит (кофе, сладость, музыка, книга), неожиданно оказавшееся рядом.",
        "воспоминание": "Задание вернуть тёплое воспоминание. Конкретика: старое фото, музыка из прошлого, воссоздание маленькой детали того дня.",
        "близость": "Задание про эмоциональную или физическую близость. НЕ 'обнимитесь'. Конкретика: что-то неожиданное и настоящее — массаж, тихий вечер, письмо, совместная тишина.",
        "игра": "Задание игровое. Конкретика: небольшое соревнование, челлендж, игра вдвоём, ставки на желание.",
        "приключение": "Задание про маленькое приключение. Конкретика: выйти вместе куда-то, сделать что-то новое, мини-квест дома или по маршруту.",
        "творчество": "Задание творческое. Конкретика: вместе приготовить новое блюдо, нарисовать что-то для партнёра, придумать совместный ритуал.",
        "забота": "Задание про заботу. Конкретика: то, что партнёру действительно нужно и приятно (уставшему — отдых, голодному — еда, грустному — поддержка).",
    }
    
    if task_type == "photo":
        prompt = (
            f"Ты — внимательный и тонкий автор романтических заданий для пар.\n\n"
            f"КОМУ ПИШЕШЬ: {user_role}. {partner_role.capitalize()}.\n"
            f"История пары: познакомились в {place}, общие увлечения — {hobbies}, любимый фильм — {movie}, "
            f"язык любви у {name} — {love_lang}.\n"
            f"Состояние отношений: {relationship_state}.\n"
            f"{duration_text}\n"
            f"{hobby_hint}\n"
            f"{state_hint}\n"
            f"{feedback_text}\n"
            f"Категория: воспоминание со старой фотографией.\n\n"
            f"ФОРМАТ ОТВЕТА (строго):\n"
            f"🎯 Название задания\n\n"
            f"Шаг 1. [конкретное действие]\n"
            f"Шаг 2. [конкретное действие]\n"
            f"Шаг 3. [конкретное действие]\n\n"
            f"⏱ Время: [5-15] минут\n\n"
            f"ЖЁСТКИЕ ПРАВИЛА:\n"
            f"— Обращайся ТОЛЬКО к {name} ({user_gender}), НЕ путай роли!\n"
            f"— Задание делает {name} для {partner_desc}.\n"
            f"— БЕЗ театральщины ('представь, что ты...', 'разыграй сцену', 'сыграй роль').\n"
            f"— БЕЗ детсадовщины ('нарисуй солнышко', 'сделай коллаж', 'сочини стишок').\n"
            f"— БЕЗ банальностей ('обнимитесь', 'посмотрите в глаза', 'скажи что любишь').\n"
            f"— Максимум 3 шага, каждый простой и выполнимый.\n"
            f"— Без markdown (никаких **, ##, *).\n"
            f"— Пиши живо, по-человечески, как будто шепчешь на ухо подруге/другу.\n"
            f"— Напиши только текст задания."
        )
    else:
        prompt = (
            f"Ты — внимательный и тонкий автор романтических заданий для пар.\n\n"
            f"КОМУ ПИШЕШЬ: {user_role}. {partner_role.capitalize()}.\n"
            f"История пары: познакомились в {place}, общие увлечения — {hobbies}, любимый фильм — {movie}.\n"
            f"Состояние отношений: {relationship_state}.\n"
            f"{duration_text}\n"
            f"{hobby_hint}\n"
            f"{state_hint}\n"
            f"{feedback_text}\n"
            f"Категория: {category}. {category_instructions[category]}\n\n"
            f"ФОРМАТ ОТВЕТА (строго):\n"
            f"🎯 Название задания\n\n"
            f"Шаг 1. [конкретное действие]\n"
            f"Шаг 2. [конкретное действие]\n"
            f"Шаг 3. [конкретное действие]\n\n"
            f"⏱ Время: [5-15] минут\n\n"
            f"ЖЁСТКИЕ ПРАВИЛА:\n"
            f"— Обращайся ТОЛЬКО к {name} ({user_gender}), НЕ путай роли!\n"
            f"— Если {name} мужчина — пиши ему как мужчине, который делает что-то для своей {partner_desc}.\n"
            f"— Если {name} женщина — пиши ей как женщине, которая делает что-то для своего {partner_desc}.\n"
            f"— Задание делает {name} для партнёра, а не наоборот.\n"
            f"— БЕЗ театральщины ('представь, что ты...', 'разыграй сцену', 'сыграй роль', 'будто вы в кино').\n"
            f"— БЕЗ детсадовщины ('нарисуй солнышко', 'сделай коллаж', 'сочини стишок', 'придумай сказку').\n"
            f"— БЕЗ банальностей ('обнимитесь покрепче', 'посмотрите в глаза', 'скажи три комплимента').\n"
            f"— Конкретика, а не абстракция. Не 'сделай приятное', а 'приготовь его любимый кофе и оставь записку на чашке'.\n"
            f"— Учитывай, что партнёру ({partner_desc}) реально будет приятно и интересно.\n"
            f"— Максимум 3 шага, каждый простой и выполнимый за 2-3 минуты.\n"
            f"— Без markdown (никаких **, ##, *).\n"
            f"— Пиши живо, тепло, по-человечески.\n"
            f"— Напиши только текст задания."
        )
    
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "Ты тонкий автор романтических заданий. Пишешь живо, конкретно, без театральности и банальностей. Всегда учитываешь пол и роль. Никогда не используешь markdown."},
            {"role": "user", "content": prompt}
        ],
        temperature=1.0
    )
    return response.choices[0].message.content.strip()


def get_task_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data="task_done")],
        [InlineKeyboardButton(text="🔄 Другое задание", callback_data="task_new"),
         InlineKeyboardButton(text="⏰ Напомнить позже", callback_data="task_later")],
        [InlineKeyboardButton(text="❌ Не понравилось", callback_data="task_dislike")]
    ])


# ============ КОМАНДЫ ============

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = database.get_user(user_id)
    
    if user:
        days = database.days_left(user_id)
        sub_status = f"🎁 Осталось {days} дн. пробного периода" if days > 0 else "🔒 Требуется подписка"
        await message.answer(
            f"💕 <b>С возвращением, {user[1]}!</b>\n\n"
            f"{sub_status}\n\n"
            "Выберите действие на кнопках ниже 👇",
            reply_markup=main_menu,
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "💕 <b>Добро пожаловать в Cupidonio!</b>\n\n"
            "Я — ваш личный помощник, который каждый день придумывает особенные задания, чтобы ваши отношения становились только крепче.\n\n"
            "✨ <b>Что я умею:</b>\n"
            "📝 Генерирую уникальные задания\n"
            "✅ Слежу за выполнением\n"
            "📊 Показываю статистику\n"
            "🗺️ Создаю карты сокровищ\n\n"
            "🎁 <b>Первые 3 дня — бесплатно!</b>\n\n"
            "Давайте познакомимся.\n"
            "<b>Как тебя зовут?</b>",
            parse_mode="HTML"
        )
        await state.set_state(RegistrationForm.name)


# --- Регистрация ---

@dp.message(RegistrationForm.name)
async def reg_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(
        "Приятно познакомиться! 👋\n\n"
        "Укажи свой пол, чтобы я мог давать правильные задания:",
        reply_markup=gender_keyboard
    )
    await state.set_state(RegistrationForm.user_gender)


@dp.message(RegistrationForm.user_gender)
async def reg_user_gender(message: types.Message, state: FSMContext):
    if message.text not in ["👨 Мужчина", "👩 Женщина"]:
        await message.answer("Пожалуйста, выбери из кнопок ниже 👇", reply_markup=gender_keyboard)
        return
    gender = "мужчина" if "Мужчина" in message.text else "женщина"
    await state.update_data(user_gender=gender)
    await message.answer(
        "Отлично! 💑\n\nКак зовут твоего партнёра?",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(RegistrationForm.partner_name)


@dp.message(RegistrationForm.partner_name)
async def reg_partner_name(message: types.Message, state: FSMContext):
    await state.update_data(partner_name=message.text)
    await message.answer(
        f"Какой пол у твоего партнёра?",
        reply_markup=gender_keyboard
    )
    await state.set_state(RegistrationForm.partner_gender)


@dp.message(RegistrationForm.partner_gender)
async def reg_partner_gender(message: types.Message, state: FSMContext):
    if message.text not in ["👨 Мужчина", "👩 Женщина"]:
        await message.answer("Пожалуйста, выбери из кнопок ниже 👇", reply_markup=gender_keyboard)
        return
    gender = "мужчина" if "Мужчина" in message.text else "женщина"
    await state.update_data(partner_gender=gender)
    await message.answer(
        "📅 Введите дату вашего знакомства в формате <b>ДД.ММ.ГГГГ</b>\n\n"
        "Например: <i>15.05.2020</i>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.set_state(RegistrationForm.meeting_date)


@dp.message(RegistrationForm.meeting_date)
async def reg_date(message: types.Message, state: FSMContext):
    await state.update_data(meeting_date=message.text)
    await message.answer(
        "📍 Где вы познакомились?\n\n"
        "Например: <i>кафе «Уют», парк Горького, университет</i>",
        parse_mode="HTML"
    )
    await state.set_state(RegistrationForm.meeting_place)


@dp.message(RegistrationForm.meeting_place)
async def reg_place(message: types.Message, state: FSMContext):
    await state.update_data(meeting_place=message.text)
    await message.answer(
        "🎯 Расскажите про ваши общие увлечения (через запятую).\n\n"
        "Например: <i>путешествия, кино, кулинария, йога</i>",
        parse_mode="HTML"
    )
    await state.set_state(RegistrationForm.hobbies)


@dp.message(RegistrationForm.hobbies)
async def reg_hobbies(message: types.Message, state: FSMContext):
    await state.update_data(hobbies=message.text)
    await message.answer("🎬 Какой ваш общий любимый фильм или книга?")
    await state.set_state(RegistrationForm.favorite_movie)


@dp.message(RegistrationForm.favorite_movie)
async def reg_movie(message: types.Message, state: FSMContext):
    await state.update_data(favorite_movie=message.text)
    await message.answer(
        "💖 Какой у вас главный язык любви?\n\nВыберите из кнопок ниже 👇",
        reply_markup=love_keyboard
    )
    await state.set_state(RegistrationForm.love_language)


@dp.message(RegistrationForm.love_language)
async def reg_love(message: types.Message, state: FSMContext):
    await state.update_data(love_language=message.text)
    await message.answer(
        "Как сейчас обстоят дела в ваших отношениях? 💭\n\n"
        "Это поможет мне подобрать правильный тон заданий:",
        reply_markup=relationship_keyboard
    )
    await state.set_state(RegistrationForm.relationship_state)


@dp.message(RegistrationForm.relationship_state)
async def reg_relationship_state(message: types.Message, state: FSMContext):
    allowed = ["💚 Всё отлично", "💛 Небольшие трудности", "🧡 Отдалились", "❤️‍🩹 Кризис"]
    if message.text not in allowed:
        await message.answer("Пожалуйста, выбери из кнопок ниже 👇", reply_markup=relationship_keyboard)
        return
    
    state_map = {
        "💚 Всё отлично": "отлично",
        "💛 Небольшие трудности": "небольшие трудности",
        "🧡 Отдалились": "отдалились",
        "❤️‍🩹 Кризис": "кризис"
    }
    relationship_state = state_map[message.text]
    await state.update_data(relationship_state=relationship_state)
    
    data = await state.get_data()
    
    database.add_user(
        user_id=message.from_user.id,
        name=data['name'],
        partner_name=data['partner_name'],
        meeting_date=data['meeting_date'],
        meeting_place=data['meeting_place'],
        hobbies=data['hobbies'],
        favorite_movie=data['favorite_movie'],
        love_language=data['love_language'],
        user_gender=data.get('user_gender', 'не указан'),
        partner_gender=data.get('partner_gender', 'не указан'),
        relationship_state=relationship_state
    )
    
    await message.answer(
        f"🎉 <b>Отлично, {data['name']}!</b>\n\n"
        "Вы успешно зарегистрированы.\n\n"
        "🎁 Вам активирован <b>бесплатный период на 3 дня</b>.\n"
        "Каждое утро в 9:00 я буду присылать ваше первое задание.\n\n"
        "А пока — попробуйте прямо сейчас 👇",
        reply_markup=main_menu,
        parse_mode="HTML"
    )
    await state.clear()


# --- Задание ---

@dp.message(Command("task"))
@dp.message(F.text == "📝 Задание")
async def cmd_task(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    if not await require_subscription(message, user_id):
        return
    
    today_task = database.get_today_task(user_id)
    
    if today_task:
        task_text, completed = today_task
        if completed:
            await message.answer(
                "✅ <b>Вы уже выполнили сегодняшнее задание!</b>\n\n"
                "Возвращайтесь завтра утром за новым 🌅\n\n"
                "А пока можете посмотреть статистику — кнопка <b>📊 Статистика</b>.",
                parse_mode="HTML"
            )
            return
        else:
            await message.answer(
                f"📝 <b>Ваше задание на сегодня:</b>\n\n"
                f"💌 {task_text}\n\n"
                "Когда выполните — нажмите кнопку ниже 👇",
                reply_markup=get_task_keyboard(),
                parse_mode="HTML"
            )
            return
    
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    wait_msg = await message.answer("✨ <i>Создаю для вас особенное задание…</i>", parse_mode="HTML")
    
    try:
        task_text = generate_task_from_ai(user_id, "text")
    except Exception as e:
        print(f"AI ERROR: {e}")
        await wait_msg.edit_text(
            "😔 <b>Не получилось создать задание</b>\n\n"
            "Попробуйте ещё раз через минуту.",
            parse_mode="HTML"
        )
        return
    
    database.save_task(user_id, task_text)
    await wait_msg.edit_text(
        f"📝 <b>Ваше задание на сегодня:</b>\n\n"
        f"💌 {task_text}\n\n"
        "Когда выполните — нажмите кнопку ниже 👇",
        reply_markup=get_task_keyboard(),
        parse_mode="HTML"
    )


# --- Inline-кнопки под заданием ---

@dp.callback_query(F.data == "task_done")
async def callback_done(callback: CallbackQuery):
    user_id = callback.from_user.id
    today_task = database.get_today_task(user_id)
    
    if not today_task:
        await callback.answer("Задания на сегодня нет.", show_alert=True)
        return
    
    task_text, completed = today_task
    if completed:
        await callback.answer("Уже выполнено ✅", show_alert=False)
        return
    
    database.mark_done(user_id)
    await callback.message.edit_text(
        "✅ <b>Отлично!</b>\n\n"
        "Задание выполнено. Вы делаете свои отношения крепче с каждым днём 💪💕",
        parse_mode="HTML"
    )
    await callback.answer("Задание выполнено!")


@dp.callback_query(F.data == "task_new")
async def callback_new(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    await callback.message.edit_text("✨ <i>Создаю новое задание…</i>", parse_mode="HTML")
    await bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    
    try:
        task_text = generate_task_from_ai(user_id, "text")
    except Exception as e:
        print(f"AI ERROR: {e}")
        await callback.message.edit_text("😔 Не получилось. Попробуйте позже.")
        await callback.answer()
        return
    
    database.save_task(user_id, task_text)
    
    await callback.message.edit_text(
        f"📝 <b>Ваше новое задание:</b>\n\n"
        f"💌 {task_text}\n\n"
        "Когда выполните — нажмите кнопку ниже 👇",
        reply_markup=get_task_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer("Новое задание готово!")


@dp.callback_query(F.data == "task_later")
async def callback_later(callback: CallbackQuery):
    await callback.answer("Ок! Напомню вечером в 19:00 ⏰", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data == "task_dislike")
async def callback_dislike(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "🤔 <b>Понял, спасибо за честность!</b>\n\n"
        "Напиши, что именно не так? Например:\n"
        "— слишком банальное\n"
        "— не подходит нам\n"
        "— хочу больше романтики\n"
        "— хочу больше игры\n\n"
        "Я учту это в следующих заданиях 👇",
        parse_mode="HTML"
    )
    await state.set_state(FeedbackForm.waiting_feedback)


@dp.message(FeedbackForm.waiting_feedback)
async def process_feedback(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    feedback = message.text.strip()
    
    today_task = database.get_today_task(user_id)
    task_text = today_task[0] if today_task else "—"
    database.save_feedback(user_id, task_text, feedback)
    
    await message.answer(
        "💾 <b>Записал!</b>\n\n"
        "Теперь буду учитывать это при генерации новых заданий. "
        "Нажми <b>📝 Задание</b>, чтобы получить свежее задание ✨",
        parse_mode="HTML"
    )
    await state.clear()

@dp.callback_query(F.data.startswith("mood_"))
async def process_mood(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    mood_map = {
        "mood_great": ("great", "😍", "отлично", "🔥 Прекрасно! Буду давать смелые, тёплые, живые задания."),
        "mood_good": ("good", "🙂", "отлично", "✨ Отлично! Сохраняю тёплый тон и добавлю немного огня."),
        "mood_hard": ("hard", "😐", "небольшие трудности", "💛 Понял. Сделаю задания мягче — без давления, только тепло."),
        "mood_crisis": ("crisis", "😔", "кризис", "❤️‍🩹 Слышу тебя. Задания будут очень аккуратными — про заботу и присутствие."),
    }
    
    mood_key, emoji, new_state, response_text = mood_map[callback.data]
    
    # Сохраняем check-in и обновляем состояние отношений
    database.update_checkin(user_id, mood_key)
    database.update_relationship_state(user_id, new_state)
    
    await callback.message.edit_text(
        f"{emoji} <b>Спасибо, что поделился.</b>\n\n"
        f"{response_text}\n\n"
        "Я всегда рядом — просто напиши /task, когда будешь готов 💕",
        parse_mode="HTML"
    )

# --- Выполнено (кнопка внизу) ---

@dp.message(Command("done"))
@dp.message(F.text == "✅ Выполнено")
async def cmd_done(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    if not await require_subscription(message, user_id):
        return
    
    today_task = database.get_today_task(user_id)
    
    if not today_task:
        await message.answer(
            "❌ <b>Сегодня задания ещё не было</b>\n\n"
            "Нажмите <b>📝 Задание</b>, чтобы получить его.",
            parse_mode="HTML"
        )
        return
    
    task_text, completed = today_task
    
    if completed:
        await message.answer(
            "ℹ️ <b>Это задание уже отмечено выполненным</b>\n\n"
            "Возвращайтесь завтра за новым 🌅",
            parse_mode="HTML"
        )
        return
    
    database.mark_done(user_id)
    await message.answer(
        "✅ <b>Отлично!</b>\n\n"
        "Задание выполнено. Вы делаете свои отношения крепче с каждым днём 💪💕",
        parse_mode="HTML"
    )


# --- Статистика ---

@dp.message(Command("stats"))
@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    if not await require_subscription(message, user_id):
        return
    
    week, month, streak, total = database.get_stats(user_id)
    
    if total == 0:
        await message.answer(
            "📊 <b>Статистика пока пустая</b>\n\n"
            "Вы ещё не выполнили ни одного задания.\n"
            "Начните прямо сейчас — нажмите <b>📝 Задание</b>!",
            parse_mode="HTML"
        )
        return
    
    flame = "🔥" * min(streak, 5)
    await message.answer(
        f"📊 <b>Ваша статистика любви</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ За неделю: <b>{week}</b>\n"
        f"✅ За месяц: <b>{month}</b>\n"
        f"🏆 Всего выполнено: <b>{total}</b>\n"
        f"🔥 Серия подряд: <b>{streak}</b> {flame}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        "Так держать! Вы делаете ваши отношения крепче 💪",
        parse_mode="HTML"
    )


# --- Карта сокровищ (только дома) ---

@dp.message(Command("treasure"))
@dp.message(F.text == "🗺️ Карта сокровищ")
async def cmd_treasure(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    if not await require_subscription(message, user_id):
        return
    
    await message.answer(
        "🗺️ <b>Карта сокровищ — приключение для двоих</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        "Я создам для вас настоящий квест с загадками и финальным сюрпризом.\n\n"
        "🏠 <b>Сколько комнат в вашей квартире?</b>\n"
        "Напишите цифру (например: 2).",
        parse_mode="HTML"
    )
    await state.set_state(TreasureForm.rooms)


@dp.message(TreasureForm.rooms)
async def treasure_rooms_input(message: types.Message, state: FSMContext):
    rooms = message.text
    user_id = message.from_user.id
    user = database.get_user(user_id)
    
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    wait_msg = await message.answer("✨ <i>Придумываю маршрут…</i>", parse_mode="HTML")
    
    try:
        prompt = (
            f"Придумай романтическую карту сокровищ для свидания дома из {rooms} комнат. "
            f"У пары увлечения: {user[5]}, любимый фильм: {user[6]}. "
            f"Опиши пошагово 4 локации (где искать записки) и финальный сюрприз. "
            f"ВАЖНО: НЕ используй markdown-разметку (никаких ###, **, *, __, `). "
            f"Используй только эмодзи и обычный текст. "
            f"Каждую локацию начинай с эмодзи и жирного заголовка через HTML-тег <b>...</b>. "
            f"Например: 📍 <b>Локация 1: Кинозал</b>. "
            f"Формат — красивый, структурированный, с эмодзи и пустыми строками между блоками."
        )
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Ты возвращаешь только чистый текст без markdown. Используй только HTML-теги <b>, <i> и эмодзи."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8
        )
        text = response.choices[0].message.content
        text = clean_markdown(text)
        await wait_msg.edit_text(
            f"🗺️ <b>Ваша карта сокровищ готова!</b>\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"{text}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            "Устройте незабываемый вечер 💕",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"AI ERROR: {e}")
        await wait_msg.edit_text(
            "😔 <b>Не получилось создать карту</b>\n\nПопробуйте ещё раз через минуту.",
            parse_mode="HTML"
        )
    
    await state.clear()


# --- Настройки ---

@dp.message(Command("settings"))
@dp.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if not await require_subscription(message, user_id):
        return
    
    freq = database.get_setting(user_id)
    current = "📅 Каждый день" if freq == "daily" else "🗓 3 раза в неделю"
    
    await message.answer(
        f"⚙️ <b>Настройки</b>\n\n"
        f"Текущая частота: <b>{current}</b>\n\n"
        "Выберите новую частоту 👇",
        reply_markup=settings_keyboard,
        parse_mode="HTML"
    )


@dp.message(Command("state"))
async def cmd_state(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user = database.get_user(user_id)
    
    if not user:
        await message.answer("Сначала пройдите регистрацию — нажмите /start")
        return
    
    current = database.get_relationship_state(user_id)
    
    await message.answer(
        f"💭 <b>Как сейчас обстоят дела в ваших отношениях?</b>\n\n"
        f"Текущий статус: <b>{current}</b>\n\n"
        "Выберите актуальный — я подстрою тон заданий 👇",
        reply_markup=relationship_keyboard,
        parse_mode="HTML"
    )

@dp.message(F.text == "💭 Состояние")
async def btn_state(message: types.Message, state: FSMContext):
    await cmd_state(message, state)


@dp.message(F.text.in_(["💚 Всё отлично", "💛 Небольшие трудности", "🧡 Отдалились", "❤️‍🩹 Кризис"]))
async def change_relationship_state(message: types.Message):
    # Проверяем, что пользователь уже зарегистрирован
    user_id = message.from_user.id
    user = database.get_user(user_id)
    
    if not user:
        # Это регистрация — обрабатывается в reg_relationship_state
        return
    
    state_map = {
        "💚 Всё отлично": "отлично",
        "💛 Небольшие трудности": "небольшие трудности",
        "🧡 Отдалились": "отдалились",
        "❤️‍🩹 Кризис": "кризис"
    }
    relationship_state = state_map[message.text]
    database.update_relationship_state(user_id, relationship_state)
    
    response_map = {
        "отлично": "🔥 Отлично! Буду давать смелые и тёплые задания — добавлю огня в ваши отношения.",
        "небольшие трудности": "💛 Понял. Сделаю задания мягче — вернём тепло потихоньку, без давления.",
        "отдалились": "🧡 Понял тебя. Буду аккуратным — маленькие шаги, чтобы снова почувствовать друг друга.",
        "кризис": "❤️‍🩹 Понял. Задания будут очень мягкими, без романтики через силу. Просто забота и тепло."
    }
    
    await message.answer(
        f"{response_map[relationship_state]}\n\n"
        "Главное меню 👇",
        reply_markup=main_menu
    )

@dp.message(F.text.in_(["📅 Каждый день", "🗓 3 раза в неделю"]))
async def set_frequency(message: types.Message):
    user_id = message.from_user.id
    if message.text == "📅 Каждый день":
        database.update_setting(user_id, "daily")
        await message.answer("✅ <b>Готово!</b> Теперь задания будут приходить каждый день.", parse_mode="HTML", reply_markup=main_menu)
    else:
        database.update_setting(user_id, "3times_week")
        await message.answer("✅ <b>Готово!</b> Теперь задания будут приходить по понедельникам, средам и пятницам.", parse_mode="HTML", reply_markup=main_menu)

@dp.message(F.text == "⬅️ Назад в меню")
async def back_to_menu(message: types.Message):
    await message.answer("Главное меню 👇", reply_markup=main_menu)


# --- Подписка ---

@dp.message(Command("subscribe"))
@dp.message(F.text == "💎 Подписка")
async def cmd_subscribe(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    days = database.days_left(user_id)
    user = database.get_user(user_id)
    
    if not user:
        await message.answer("Сначала пройдите регистрацию — нажмите /start")
        return
    
    if days > 0:
        status = f"🎁 Пробный период: осталось <b>{days}</b> дн.\n\n"
    else:
        status = "🔒 Пробный период закончился.\n\n"
    
    await message.answer(
        f"💎 <b>Подписка Cupidonio</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{status}"
        f"📅 <b>1 месяц — {PRICE} ₽</b>\n"
        f"💡 Это меньше 10 ₽ в день за крепкие отношения.\n"
        f"━━━━━━━━━━━━━━━\n\n"
        "<b>Что входит в подписку:</b>\n"
        "📝 Ежедневные персональные задания\n"
        "🗺️ Карты сокровищ для свиданий\n"
        "📊 Статистика ваших успехов\n"
        "⚙️ Гибкая настройка частоты\n\n"
        "<b>Как оплатить:</b>\n"
        "1️⃣ Переведите 300 ₽ по номеру: <code>+7 XXX XXX-XX-XX</code>\n"
        "2️⃣ Нажмите /confirm и пришлите скриншот чека\n\n"
        "<i>Подписка активируется в течение 5 минут после проверки оплаты.</i>",
        parse_mode="HTML"
    )


@dp.message(Command("confirm"))
async def cmd_confirm(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📸 <b>Отлично!</b>\n\n"
        "Пришлите скриншот чека об оплате — и мы активируем вашу подписку в течение 5 минут.",
        parse_mode="HTML"
    )


# ============ ПЛАНИРОВЩИК ============

async def send_checkin_reminders():
    """Раз в 3 дня мягко спрашивает, как у пары дела."""
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cur.execute('SELECT user_id FROM users WHERE subscription_end >= ?', (today,))
    users = cur.fetchall()
    conn.close()
    
    for (user_id,) in users:
        # Проверяем, сколько дней прошло с последнего check-in
        days = database.days_since_checkin(user_id)
        if days < 3:
            continue
        
        try:
            await bot.send_message(
                user_id,
                "💭 <b>Как у вас сейчас?</b>\n\n"
                "Иногда полезно остановиться и честно спросить себя:\n"
                "как наши отношения? Что чувствуем друг к другу?\n\n"
                "Ответь одним нажатием — я подстрою тон заданий 👇",
                reply_markup=mood_keyboard,
                parse_mode="HTML"
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Ошибка check-in {user_id}: {e}")

async def send_daily_tasks():
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cur.execute('SELECT user_id FROM users WHERE subscription_end >= ?', (today,))
    users = cur.fetchall()
    conn.close()
    
    for (user_id,) in users:
        freq = database.get_setting(user_id)
        if freq == "3times_week":
            weekday = datetime.now().weekday()
            if weekday not in [0, 2, 4]:
                continue
        
        existing = database.get_today_task(user_id)
        if existing:
            continue
        
        try:
            task_text = generate_task_from_ai(user_id, "text")
            if not task_text:
                continue
            database.save_task(user_id, task_text)
            await bot.send_message(
                user_id,
                f"🌅 <b>Доброе утро!</b>\n\n"
                f"📝 <b>Ваше задание на сегодня:</b>\n\n"
                f"💌 {task_text}\n\n"
                "Когда выполните — нажмите <b>✅ Выполнено</b>.",
                parse_mode="HTML"
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Ошибка отправки пользователю {user_id}: {e}")


scheduler.add_job(send_daily_tasks, "cron", hour=9, minute=0)


async def send_evening_reminder():
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cur.execute('''
        SELECT c.user_id FROM completed_tasks c
        WHERE c.task_date = ? AND c.completed = 0
    ''', (today,))
    users = cur.fetchall()
    conn.close()
    
    for (user_id,) in users:
        try:
            await bot.send_message(
                user_id,
                "🌙 <b>Напоминание</b>\n\n"
                "Вы ещё не отметили задание на сегодня. Оно займёт всего 5 минут — "
                "а вечер станет теплее 💕\n\n"
                "Нажмите <b>📝 Задание</b>, чтобы посмотреть его, "
                "или <b>✅ Выполнено</b>, если уже сделали.",
                parse_mode="HTML"
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Ошибка напоминания {user_id}: {e}")


scheduler.add_job(send_evening_reminder, "cron", hour=19, minute=0)
scheduler.add_job(send_checkin_reminders, "cron", hour=11, minute=0)

# ============ ВЕБ-СЕРВЕР ============

async def health_check(request):
    return web.Response(text="OK")


async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', 10000))
    site = web.TCPSite(runner, '0.0.0.0', port=port)
    await site.start()
    print(f"🌐 Веб-сервер запущен на порту {port}")


# ============ ЗАПУСК ============

async def main():
    asyncio.create_task(start_web_server())
    scheduler.start()
    print("🤖 Бот Купидон запущен и ждёт команды...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())