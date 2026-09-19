import aiohttp
import urllib.parse
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
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openai import OpenAI
from aiohttp import web
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

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

# --- FSM ---
class RegistrationForm(StatesGroup):
    name = State()
    partner_name = State()
    meeting_date = State()
    meeting_place = State()
    hobbies = State()
    favorite_movie = State()
    love_language = State()

class TreasureForm(StatesGroup):
    scale = State()
    budget = State()
    rooms = State()
    city = State()
class FeedbackForm(StatesGroup):
    waiting_feedback = State()

# --- Клавиатуры ---
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Задание"), KeyboardButton(text="✅ Выполнено")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🗺️ Карта сокровищ")],
        [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="💎 Подписка")],
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

settings_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Каждый день")],
        [KeyboardButton(text="🗓 3 раза в неделю")],
        [KeyboardButton(text="⬅️ Назад в меню")]
    ],
    resize_keyboard=True
)

# --- Хелпер: проверка подписки ---
async def require_subscription(message: types.Message, user_id: int) -> bool:
    if not database.check_subscription(user_id):
        await message.answer(
            "🔒 <b>Доступ приостановлен</b>\n\n"
            "Ваш пробный период закончился, и теперь все задания доступны только по подписке.\n\n"
            "💎 <b>Всего 300 ₽ в месяц</b> — это меньше 10 рублей в день за крепкие отношения.\n\n"
            "Нажмите /subscribe, чтобы оформить подписку.",
            parse_mode="HTML"
        )
        return False
    return True

# --- Хелпер: генерация задания ---

async def search_places(query: str, city: str = None, limit: int = 5):
    """
    Ищет реальные места через OpenStreetMap Nominatim.
    Возвращает список словарей: [{name, address, lat, lon}, ...]
    """
    if city:
        query = f"{query}, {city}"
    
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": limit,
        "addressdetails": 1,
        "accept-language": "ru"
    }
    headers = {"User-Agent": "CupidonioBot/1.0"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = []
                    for item in data:
                        results.append({
                            "name": item.get("display_name", "").split(",")[0],
                            "address": item.get("display_name", ""),
                            "lat": item.get("lat"),
                            "lon": item.get("lon"),
                            "type": item.get("type", "")
                        })
                    return results
    except Exception as e:
        print(f"OSM search error: {e}")
    return []


def format_places_for_prompt(places: list) -> str:
    """Превращает список мест в текст для промпта."""
    if not places:
        return "Реальные места не найдены, используй общие описания без конкретных адресов."
    
    lines = []
    for i, p in enumerate(places, 1):
        lines.append(f"{i}. {p['name']} — {p['address']} (координаты: {p['lat']}, {p['lon']})")
    return "\n".join(lines)

def generate_task_from_ai(user_id, task_type="text", category=None):
    user = database.get_user(user_id)
    if not user:
        return None
    
    name, partner, meeting_date, place, hobbies, movie, love_lang = user[1], user[2], user[3], user[4], user[5], user[6], user[7]
    
    feedback_list = database.get_recent_feedback(user_id, limit=3)
    feedback_text = ""
    if feedback_list:
        feedback_text = "\n\nВАЖНО! Пользователь ранее жаловался на такие вещи в заданиях (НЕ повторяй):\n"
        for i, fb in enumerate(feedback_list, 1):
            feedback_text += f"{i}. {fb}\n"
    
    if not category:
        categories = ["разговор", "сюрприз", "воспоминание", "близость", "игра", "приключение", "творчество"]
        import random
        category = random.choice(categories)
    
    category_instructions = {
        "разговор": "Квест должен привести пару к глубокому, неожиданному разговору. Не просто 'поговорите о чувствах', а конкретный сценарий: например, вопрос из конверта, игра в 'правда или действие', обмен мечтами на год вперёд.",
        "сюрприз": "Квест должен быть про тайный сюрприз для партнёра. Конкретика: спрятанная записка, неожиданный звонок, маленький подарок без повода, завтрак в постель.",
        "воспоминание": "Квест должен вернуть пару в тёплое прошлое. Конкретика: найти старое фото, воссоздать момент первого свидания, написать письмо 'в прошлое' себе или партнёру.",
        "близость": "Квест должен быть про близость, но ИНТЕРЕСНЫЙ, а не банальный. Не 'обнимитесь', а, например, 'расскажи партнёру 3 вещи, которые ты в нём обожаешь, глядя в глаза'.",
        "игра": "Квест должен быть игровым. Конкретика: настольная игра на ставки, челлендж, соревнование, квест с загадками.",
        "приключение": "Квест должен быть про приключение — можно выйти из дома или сделать приключение внутри квартиры. Конкретика: секретная миссия, поиск сокровищ, исследование нового места.",
        "творчество": "Квест должен быть творческим. Конкретика: вместе приготовить новое блюдо, нарисовать друг друга, придумать песню, написать совместный рассказ.",
    }
    
    if task_type == "photo":
        prompt = (
            f"Ты — креативный автор романтических квестов для пар. Придумай ОДИН интересный МНОГОШАГОВЫЙ квест.\n\n"
            f"Данные пары:\n— Имя: {name}\n— Партнёра: {partner}\n— Познакомились: {place}\n"
            f"— Увлечения: {hobbies}\n— Любимый фильм: {movie}\n— Язык любви: {love_lang}\n\n"
            f"Категория: воспоминание (со старой фотографией)."
            f"{feedback_text}\n\n"
            f"ФОРМАТ ОТВЕТА (строго):\n"
            f"🎯 Квест «[название]»\n\n"
            f"Шаг 1. [конкретное действие]\n"
            f"Шаг 2. [конкретное действие]\n"
            f"Шаг 3. [конкретное действие]\n\n"
            f"⏱ Время: [X] минут\n"
            f"💡 Фишка: [одна фраза — почему это интересно]\n\n"
            f"ТРЕБОВАНИЯ:\n"
            f"— Каждый шаг конкретный, выполнимый\n"
            f"— Не банально, не про объятия и взгляды\n"
            f"— Упомяни имена или историю пары\n"
            f"— Без markdown (никаких **, ##, *)"
        )
    else:
        prompt = (
            f"Ты — креативный автор романтических квестов для пар. Придумай ОДИН интересный МНОГОШАГОВЫЙ квест.\n\n"
            f"Данные пары:\n— Имя: {name}\n— Партнёра: {partner}\n— Познакомились: {place}\n"
            f"— Увлечения: {hobbies}\n— Любимый фильм: {movie}\n— Язык любви: {love_lang}\n\n"
            f"Категория: {category}.\n{category_instructions[category]}"
            f"{feedback_text}\n\n"
            f"ФОРМАТ ОТВЕТА (строго):\n"
            f"🎯 Квест «[название]»\n\n"
            f"Шаг 1. [конкретное действие]\n"
            f"Шаг 2. [конкретное действие]\n"
            f"Шаг 3. [конкретное действие]\n\n"
            f"⏱ Время: [X] минут\n"
            f"💡 Фишка: [одна фраза — почему это интересно]\n\n"
            f"ТРЕБОВАНИЯ:\n"
            f"— 3 чётких шага, каждый выполнимый за 5 минут\n"
            f"— ЗАПРЕЩЕНО: задания в стиле 'обнимитесь', 'посмотрите в глаза', 'скажите что любите'. Это банально!\n"
            f"— Используй их историю: место знакомства, увлечения, фильм\n"
            f"— Добавь интригу, неожиданность, конкретику (числа, предметы, места)\n"
            f"— Без markdown (никаких **, ##, *)\n"
            f"— Пиши на русском, тепло, но без соплей"
        )
    
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": "Ты креативный автор романтических квестов. Пишешь живо, конкретно, без банальностей. Никогда не используешь markdown."},
            {"role": "user", "content": prompt}
        ],
        temperature=1.1
    )
    return response.choices[0].message.content.strip()

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
            "📝 Генерирую уникальные задания на основе вашей истории\n"
            "✅ Слежу за выполнением\n"
            "📊 Показываю статистику\n"
            "🗺️ Создаю карты сокровищ для свиданий\n\n"
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
    await message.answer("💑 Как зовут твоего партнёра?")
    await state.set_state(RegistrationForm.partner_name)

@dp.message(RegistrationForm.partner_name)
async def reg_partner(message: types.Message, state: FSMContext):
    await state.update_data(partner_name=message.text)
    await message.answer("📅 Введите дату вашего знакомства в формате <b>ДД.ММ.ГГГГ</b>\n\nНапример: <i>15.05.2020</i>", parse_mode="HTML")
    await state.set_state(RegistrationForm.meeting_date)

@dp.message(RegistrationForm.meeting_date)
async def reg_date(message: types.Message, state: FSMContext):
    await state.update_data(meeting_date=message.text)
    await message.answer("📍 Где вы познакомились?\n\nНапример: <i>кафе «Уют», парк Горького, университет</i>", parse_mode="HTML")
    await state.set_state(RegistrationForm.meeting_place)

@dp.message(RegistrationForm.meeting_place)
async def reg_place(message: types.Message, state: FSMContext):
    await state.update_data(meeting_place=message.text)
    await message.answer("🎯 Расскажите про ваши общие увлечения (через запятую).\n\nНапример: <i>путешествия, кино, кулинария, йога</i>", parse_mode="HTML")
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
    data = await state.get_data()
    
    database.add_user(
        user_id=message.from_user.id,
        name=data['name'],
        partner_name=data['partner_name'],
        meeting_date=data['meeting_date'],
        meeting_place=data['meeting_place'],
        hobbies=data['hobbies'],
        favorite_movie=data['favorite_movie'],
        love_language=data['love_language']
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
    
    # Inline-кнопки под заданием
    task_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data="task_done")],
        [InlineKeyboardButton(text="🔄 Другое задание", callback_data="task_new"),
         InlineKeyboardButton(text="⏰ Напомнить позже", callback_data="task_later")],
        [InlineKeyboardButton(text="❌ Не понравилось", callback_data="task_dislike")]
    ])
    
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
                reply_markup=task_keyboard,
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
            "Попробуйте ещё раз через минуту. Если ошибка повторяется — напишите нам.",
            parse_mode="HTML"
        )
        return
    
    database.save_task(user_id, task_text)
    await wait_msg.edit_text(
        f"📝 <b>Ваше задание на сегодня:</b>\n\n"
        f"💌 {task_text}\n\n"
        "Когда выполните — нажмите кнопку ниже 👇",
        reply_markup=task_keyboard,
        parse_mode="HTML"
    )

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
    
    if not await require_subscription(callback.message, user_id):
        await callback.answer()
        return
    
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
    
    task_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data="task_done")],
        [InlineKeyboardButton(text="🔄 Другое задание", callback_data="task_new"),
         InlineKeyboardButton(text="⏰ Напомнить позже", callback_data="task_later")],
        [InlineKeyboardButton(text="❌ Не понравилось", callback_data="task_dislike")]
    ])
    
    await callback.message.edit_text(
        f"📝 <b>Ваше новое задание:</b>\n\n"
        f"💌 {task_text}\n\n"
        "Когда выполните — нажмите кнопку ниже 👇",
        reply_markup=task_keyboard,
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
    
    # Сохраняем фидбек
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

# --- Выполнено ---
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

# --- Карта сокровищ ---
@dp.message(Command("treasure"))
@dp.message(F.text == "🗺️ Карта сокровищ")
async def cmd_treasure(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    if not await require_subscription(message, user_id):
        return
    
    scale_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Дома", callback_data="treasure_home")],
        [InlineKeyboardButton(text="🏙 По городу", callback_data="treasure_city")],
        [InlineKeyboardButton(text="✈️ Путешествие", callback_data="treasure_trip")],
    ])
    
    await message.answer(
        "🗺️ <b>Карта сокровищ — приключение для двоих</b>\n"
        "━━━━━━━━━━━━━━━\n\n"
        "Я создам для вас настоящий квест с загадками, локациями и финальным сюрпризом.\n\n"
        "Сначала выберите <b>масштаб приключения</b> 👇",
        reply_markup=scale_keyboard,
        parse_mode="HTML"
    )
    await state.set_state(TreasureForm.scale)


@dp.callback_query(F.data == "treasure_home")
async def treasure_home(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(scale="home")
    await callback.message.edit_text(
        "🏠 <b>Домашнее приключение</b>\n\n"
        "Сколько комнат в вашей квартире? Напишите цифру (например: 2).",
        parse_mode="HTML"
    )
    await state.set_state(TreasureForm.rooms)


@dp.callback_query(F.data == "treasure_city")
async def treasure_city(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(scale="city")
    await callback.message.edit_text(
        "🏙 <b>Приключение по городу</b>\n\n"
        "Напишите название вашего города (например: Москва).",
        parse_mode="HTML"
    )
    await state.set_state(TreasureForm.city)


@dp.callback_query(F.data == "treasure_trip")
async def treasure_trip(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(scale="trip")
    await callback.message.edit_text(
        "✈️ <b>Приключение в путешествии</b>\n\n"
        "Куда вы едете или хотите поехать? Напишите город или страну.",
        parse_mode="HTML"
    )
    await state.set_state(TreasureForm.city)


@dp.message(TreasureForm.city)
async def treasure_city_input(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text)
    await ask_budget(message, state)


@dp.message(TreasureForm.rooms)
async def treasure_rooms_input(message: types.Message, state: FSMContext):
    await state.update_data(rooms=message.text)
    await ask_budget(message, state)


async def ask_budget(message: types.Message, state: FSMContext):
    budget_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆓 Бесплатно (0 ₽)", callback_data="budget_free")],
        [InlineKeyboardButton(text="💵 До 1000 ₽", callback_data="budget_low")],
        [InlineKeyboardButton(text="💰 До 5000 ₽", callback_data="budget_mid")],
        [InlineKeyboardButton(text="💎 Без ограничений", callback_data="budget_high")],
    ])
    await message.answer(
        "💸 <b>Какой у вас бюджет на это приключение?</b>",
        reply_markup=budget_keyboard,
        parse_mode="HTML"
    )
    await state.set_state(TreasureForm.budget)


@dp.callback_query(F.data.startswith("budget_"))
async def treasure_budget(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    budget_map = {
        "budget_free": "бесплатно (только подручные средства, записки, подсказки дома или на улице)",
        "budget_low": "до 1000 рублей (кофе, небольшой подарок, цветы)",
        "budget_mid": "до 5000 рублей (ужин, билеты, небольшой сюрприз)",
        "budget_high": "без ограничений (можно арендовать, заказать, купить серьёзный подарок)"
    }
    budget = budget_map[callback.data]
    await state.update_data(budget=budget)
    
    data = await state.get_data()
    scale = data.get("scale", "home")
    rooms = data.get("rooms", "2")
    city = data.get("city", "ваш город")
    
    await callback.message.edit_text("✨ <i>Создаю для вас настоящее приключение…</i>", parse_mode="HTML")
    await bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    
    user_id = callback.from_user.id
    user = database.get_user(user_id)
    
    if scale == "home":
        location_info = f"приключение дома, в квартире из {rooms} комнат"
    elif scale == "city":
        location_info = f"приключение по городу {city}"
    else:
        location_info = f"приключение в путешествии ({city})"
    
    # Ищем реальные места в городе через OpenStreetMap
    real_places_text = ""
    if scale == "city" and city:
        # Ищем романтичные места
        parks = await search_places("парк", city, limit=3)
        embankments = await search_places("набережная", city, limit=2)
        cafes = await search_places("кафе", city, limit=2)
        all_places = parks + embankments + cafes
        real_places_text = f"\n\nРЕАЛЬНЫЕ МЕСТА В ГОРОДЕ (используй ТОЛЬКО их, не выдумывай!):\n{format_places_for_prompt(all_places)}"
    elif scale == "trip" and city:
        real_places_text = f"\n\nГород: {city}. Используй только реальные, известные места. Не выдумывай адреса."

    prompt = (
        f"Ты — автор захватывающих романтических квестов. Создай ПОЛНОЦЕННОЕ приключение для пары.\n\n"
        f"Данные пары:\n— Имя: {user[1]}\n— Партнёр: {user[2]}\n— Увлечения: {user[5]}\n— Любимый фильм: {user[6]}\n\n"
        f"Формат: {location_info}.\n"
        f"Бюджет: {budget}.\n\n"
        f"{real_places_text}\n\n"
        f"СОЗДАЙ КВЕСТ по такой структуре:\n\n"
        f"🗺️ <b>Название приключения</b>\n"
        f"[Красивое, интригующее название]\n\n"
        f"📜 <b>Легенда</b>\n"
        f"[2-3 предложения — вступление: почему они отправляются в путь, что ищут]\n\n"
        f"🎯 <b>Цель</b>\n"
        f"[Что должны найти в конце]\n\n"
        f"📍 <b>Локация 1: [название]</b>\n"
        f"[Что там делать, какую записку найти, какую загадку разгадать]\n\n"
        f"📍 <b>Локация 2: [название]</b>\n"
        "[...]\n\n"
        f"📍 <b>Локация 3: [название]</b>\n"
        "[...]\n\n"
        f"📍 <b>Локация 4: [название]</b>\n"
        "[...]\n\n"
        f"🎁 <b>Финальный сюрприз</b>\n"
        f"[Что ждёт их в конце — конкретно, с учётом бюджета]\n\n"
        f"💡 <b>Совет</b>\n"
        f"[Одна идея по атмосфере: музыка, свет, время дня]\n\n"
        f"ТРЕБОВАНИЯ:\n"
        f"— Каждая локация конкретная (что делать, где искать, что найти)\n"
        f"— Загадки и подсказки, а не просто 'обнимитесь'\n"
        f"— Упомяни их увлечения ({user[5]}) и любимый фильм ({user[6]})\n"
        f"— Учитывай бюджет: {budget}\n"
        f"— Без markdown (никаких ###, **, *)\n"
        f"— Используй только HTML-теги <b> и <i> и эмодзи\n"
        f"— Пиши живо, атмосферно, как в хорошем квесте"
        f"\nВАЖНО: Не выдумывай названия мест и адреса. Используй только реальные данные из списка выше или общеизвестные места."
    )
    
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Ты автор романтических квестов. Пишешь атмосферно, конкретно. Используешь только HTML-теги <b>, <i> и эмодзи."},
                {"role": "user", "content": prompt}
            ],
            temperature=1.0
        )
        text = response.choices[0].message.content
        text = clean_markdown(text)
        
        # Добавляем ссылку на карту, если есть город
        map_link = ""
        if scale == "city" and city:
            encoded_city = urllib.parse.quote(city)
            map_link = f"\n\n📍 <b>Открыть карту города:</b> https://www.google.com/maps/search/{encoded_city}"
        
        text = text + map_link

        # Разбиваем длинное сообщение на части (Telegram лимит 4096 символов)
        full_text = f"🗺️ <b>Ваше приключение готово!</b>\n━━━━━━━━━━━━━━━\n\n{text}"
        
        if len(full_text) <= 4000:
            await callback.message.edit_text(full_text, parse_mode="HTML")
        else:
            # Отправляем по частям
            await callback.message.edit_text("🗺️ <b>Ваше приключение готово!</b>", parse_mode="HTML")
            chunks = [text[i:i+3800] for i in range(0, len(text), 3800)]
            for chunk in chunks:
                await callback.message.answer(chunk, parse_mode="HTML")
        
        await callback.message.answer(
            "━━━━━━━━━━━━━━━\n"
            "Устройте незабываемое приключение 💕\n\n"
            "Хотите другое? Нажмите <b>🗺️ Карта сокровищ</b> снова.",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"AI ERROR: {e}")
        await callback.message.edit_text(
            "😔 <b>Не получилось создать приключение</b>\n\nПопробуйте через минуту.",
            parse_mode="HTML"
        )
    
    await state.clear()

def clean_markdown(text: str) -> str:
    """Убирает markdown-символы и превращает их в читаемый текст."""
    import re
    # Заголовки ###, ##, # -> убираем и добавляем эмодзи-разделитель
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Жирный **текст** и __текст__ -> оставляем текст
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # Курсив *текст* и _текст_ -> оставляем текст
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'\1', text)
    # Горизонтальные линии --- -> длинная черта
    text = re.sub(r'^-{3,}$', '─────────────', text, flags=re.MULTILINE)
    # Ссылки [текст](url) -> текст
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    # Код `текст` -> текст
    text = re.sub(r'`(.+?)`', r'\1', text)
    # Убираем лишние пустые строки
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

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
    
    if not user:
        await message.answer("Сначала пройдите регистрацию — нажмите /start")
        return
    
    status = ""
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
async def cmd_confirm(message: types.Message):
    await message.answer(
        "📸 <b>Отлично!</b>\n\n"
        "Пришлите скриншот чека об оплате — и мы активируем вашу подписку в течение 5 минут.",
        parse_mode="HTML"
    )

# ============ ПЛАНИРОВЩИК ============

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
    await_dummy = None
    asyncio.run(main())