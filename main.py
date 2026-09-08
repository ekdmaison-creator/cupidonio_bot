import asyncio
import os
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from openai import OpenAI
from aiohttp import web  # <--- НОВЫЙ ИМПОРТ для веб-сервера

# Подгружаем секреты из файла .env
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    raise ValueError("BOT_TOKEN или DEEPSEEK_API_KEY не найдены в .env файле!")

# Инициализация бота и базы данных
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

# Подключаем DeepSeek
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

import database

# --- FSM для регистрации ---
class RegistrationForm(StatesGroup):
    name = State()
    partner_name = State()
    meeting_date = State()
    meeting_place = State()
    hobbies = State()
    favorite_movie = State()
    love_language = State()

class TreasureForm(StatesGroup):
    rooms = State()

# Клавиатуры
love_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Слова"), KeyboardButton(text="Время")],
        [KeyboardButton(text="Подарки"), KeyboardButton(text="Помощь")],
        [KeyboardButton(text="Прикосновения")]
    ],
    resize_keyboard=True
)

settings_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Каждый день")],
        [KeyboardButton(text="3 раза в неделю (пн, ср, пт)")]
    ],
    resize_keyboard=True
)

# --- /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = database.get_user(user_id)
    
    if user:
        await message.answer(
            f"✨ Привет, {user[1]}! Ты уже зарегистрирован(а).\n"
            "Вот что я умею:\n"
            "/task - получить сегодняшнее задание\n"
            "/done - отметить задание выполненным\n"
            "/stats - посмотреть статистику\n"
            "/treasure - карта сокровищ для свидания\n"
            "/settings - настроить частоту заданий\n"
            "/subscribe - продлить подписку"
        )
    else:
        await message.answer(
            "❤️ Привет! Давай познакомимся поближе, чтобы я мог придумывать для вас особенные задания.\n"
            "Как тебя зовут? (Напиши своё имя)"
        )
        await state.set_state(RegistrationForm.name)

# --- Обработка регистрации ---
@dp.message(RegistrationForm.name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Как зовут твоего партнёра/партнёршу?")
    await state.set_state(RegistrationForm.partner_name)

@dp.message(RegistrationForm.partner_name)
async def process_partner_name(message: types.Message, state: FSMContext):
    await state.update_data(partner_name=message.text)
    await message.answer("Введите дату вашего знакомства в формате ДД.ММ.ГГГГ (например, 15.05.2020)")
    await state.set_state(RegistrationForm.meeting_date)

@dp.message(RegistrationForm.meeting_date)
async def process_meeting_date(message: types.Message, state: FSMContext):
    await state.update_data(meeting_date=message.text)
    await message.answer("Где вы познакомились? (Например: кафе 'Уют', парк Горького, институт)")
    await state.set_state(RegistrationForm.meeting_place)

@dp.message(RegistrationForm.meeting_place)
async def process_meeting_place(message: types.Message, state: FSMContext):
    await state.update_data(meeting_place=message.text)
    await message.answer("Расскажи про ваши общие увлечения (через запятую).\nНапример: путешествия, кино, кулинария, йога")
    await state.set_state(RegistrationForm.hobbies)

@dp.message(RegistrationForm.hobbies)
async def process_hobbies(message: types.Message, state: FSMContext):
    await state.update_data(hobbies=message.text)
    await message.answer("Какой ваш общий любимый фильм или книга?")
    await state.set_state(RegistrationForm.favorite_movie)

@dp.message(RegistrationForm.favorite_movie)
async def process_movie(message: types.Message, state: FSMContext):
    await state.update_data(favorite_movie=message.text)
    await message.answer(
        "Какой у вас главный язык любви? Выбери из кнопок ниже:",
        reply_markup=love_keyboard
    )
    await state.set_state(RegistrationForm.love_language)

@dp.message(RegistrationForm.love_language)
async def process_love_language(message: types.Message, state: FSMContext):
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
        f"🎉 Отлично, {data['name']}! Ты зарегистрирован(а)!\n"
        "У тебя активирован бесплатный период на 3 дня.\n"
        "Каждое утро в 9:00 я буду присылать тебе задание.\n"
        "А пока попробуй команду /task",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()

# --- Генерация задания через DeepSeek ---
def generate_task_from_ai(user_id, task_type="text"):
    user = database.get_user(user_id)
    if not user:
        return "Ошибка: пользователь не найден."
    
    name, partner, meeting_date, place, hobbies, movie, love_lang = user[1], user[2], user[3], user[4], user[5], user[6], user[7]
    
    if task_type == "photo":
        prompt = f"Придумай романтическое задание для пары. Они познакомились в {place}, любят {hobbies}, их любимый фильм {movie}, язык любви - {love_lang}. Попроси их найти старое совместное фото из прошлого и отправить его партнёру с тёплыми словами. Напиши только текст задания, конкретное и нежное, на 1-2 предложения."
    else:
        prompt = f"Придумай простое, но очень тёплое и нешаблонное задание для пары. Они познакомились в {place}, обожают {hobbies}, их любимый фильм - {movie}. Задание должно занимать 5 минут. Упомяни их историю. Напиши только текст задания, начни с имени {name}."
    
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9
    )
    return response.choices[0].message.content

# --- /task ---
@dp.message(Command("task"))
async def cmd_task(message: types.Message):
    user_id = message.from_user.id
    if not database.check_subscription(user_id):
        await message.answer("⏳ Ваша подписка истекла! Используйте /subscribe для продления.")
        return
    task_text = generate_task_from_ai(user_id, "text")
    database.save_task(user_id, task_text)
    await message.answer(f"📝 Твоё задание на сегодня:\n\n{task_text}\n\nПосле выполнения напиши /done")

# --- /treasure ---
@dp.message(Command("treasure"))
async def cmd_treasure(message: types.Message, state: FSMContext):
    await message.answer("🏠 Сколько комнат в вашей квартире/доме? Напиши цифру.")
    await state.set_state(TreasureForm.rooms)

@dp.message(TreasureForm.rooms)
async def process_treasure_rooms(message: types.Message, state: FSMContext):
    rooms = message.text
    user_id = message.from_user.id
    user = database.get_user(user_id)
    prompt = f"Придумай романтическую карту сокровищ для свидания дома из {rooms} комнат. У пары увлечения: {user[5]}, любимый фильм: {user[6]}. Опиши пошагово 4 локации (где искать записки) и финальный сюрприз."
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8
    )
    await message.answer(f"🗺️ Карта сокровищ готова!\n\n{response.choices[0].message.content}")
    await state.clear()

# --- /done ---
@dp.message(Command("done"))
async def cmd_done(message: types.Message):
    user_id = message.from_user.id
    if database.mark_done(user_id):
        await message.answer("✅ Отлично! Задание выполнено! Продолжайте радовать друг друга ❤️")
    else:
        await message.answer("❌ Сегодня задания ещё не было (или оно уже отмечено). Сначала получи его через /task")

# --- /stats ---
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    week, month, streak = database.get_stats(user_id)
    await message.answer(
        f"📊 *Ваша статистика любви:*\n"
        f"✅ За неделю: {week} заданий\n"
        f"✅ За месяц: {month} заданий\n"
        f"🔥 Текущая серия: {streak} дней подряд!\n\n"
        f"Так держать! Ты делаешь ваши отношения крепче 💪",
        parse_mode="Markdown"
    )

# --- /settings ---
@dp.message(Command("settings"))
async def cmd_settings(message: types.Message):
    await message.answer("Выбери частоту получения заданий:", reply_markup=settings_keyboard)

@dp.message(F.text.in_(["Каждый день", "3 раза в неделю (пн, ср, пт)"]))
async def process_settings(message: types.Message):
    user_id = message.from_user.id
    if message.text == "Каждый день":
        database.update_setting(user_id, "daily")
        await message.answer("✅ Теперь задания будут приходить каждый день!")
    else:
        database.update_setting(user_id, "3times_week")
        await message.answer("✅ Теперь задания будут приходить по понедельникам, средам и пятницам!")
    await message.answer("Настройка сохранена!", reply_markup=ReplyKeyboardRemove())

# --- /subscribe ---
@dp.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message):
    await message.answer(
        "💎 *Подписка на месяц — 1500 руб.*\n\n"
        "Чтобы продлить доступ, отправьте 1500 руб на карту *XXXX XXXX XXXX XXXX* (или ссылку для оплаты).\n"
        "После оплаты напишите /confirm и ваш код, и я продлю подписку.\n\n"
        "*(Пока что это ручной режим, в следующем обновлении добавлю Telegram Stars)*",
        parse_mode="Markdown"
    )

# --- Планировщик ежедневных заданий ---
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
        try:
            task_text = generate_task_from_ai(user_id, "text")
            database.save_task(user_id, task_text)
            await bot.send_message(user_id, f"🌅 Доброе утро! Твоё задание на сегодня:\n\n{task_text}\n\nПосле выполнения напиши /done")
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Ошибка отправки пользователю {user_id}: {e}")

scheduler.add_job(send_daily_tasks, "cron", hour=9, minute=0)

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER (чтобы порт был открыт) ---
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

# --- Запуск ---
async def main():
    # Запускаем веб-сервер в фоне (чтобы Render не убивал процесс)
    asyncio.create_task(start_web_server())
    
    scheduler.start()
    print("🤖 Бот Купидон запущен и ждёт команды...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())