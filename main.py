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
    rooms = State()

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
def generate_task_from_ai(user_id, task_type="text"):
    user = database.get_user(user_id)
    if not user:
        return None
    name, partner, meeting_date, place, hobbies, movie, love_lang = user[1], user[2], user[3], user[4], user[5], user[6], user[7]
    
    if task_type == "photo":
        prompt = f"Придумай романтическое задание для пары. Они познакомились в {place}, любят {hobbies}, их любимый фильм {movie}, язык любви — {love_lang}. Попроси их найти старое совместное фото и отправить партнёру с тёплыми словами. Напиши только текст задания, 1-2 предложения."
    else:
        prompt = f"Придумай простое, но очень тёплое и нешаблонное задание для пары. Они познакомились в {place}, обожают {hobbies}, их любимый фильм — {movie}. Задание на 5 минут. Упомяни их историю. Напиши только текст задания, начни с имени {name}."
    
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9
    )
    return response.choices[0].message.content

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
async def cmd_task(message: types.Message):
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
                "Когда выполните — нажмите <b>✅ Выполнено</b>.",
                parse_mode="HTML"
            )
            return
    
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
        "Когда выполните — нажмите <b>✅ Выполнено</b>.",
        parse_mode="HTML"
    )

# --- Выполнено ---
@dp.message(Command("done"))
@dp.message(F.text == "✅ Выполнено")
async def cmd_done(message: types.Message):
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
async def cmd_stats(message: types.Message):
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
    user_id = message.from_user.id
    
    if not await require_subscription(message, user_id):
        return
    
    await message.answer(
        "🗺️ <b>Создаём карту сокровищ для свидания дома</b>\n\n"
        "Сколько комнат в вашей квартире? Напишите цифру.",
        parse_mode="HTML"
    )
    await state.set_state(TreasureForm.rooms)

@dp.message(TreasureForm.rooms)
async def treasure_rooms(message: types.Message, state: FSMContext):
    rooms = message.text
    user_id = message.from_user.id
    user = database.get_user(user_id)
    
    wait_msg = await message.answer("✨ <i>Придумываю маршрут…</i>", parse_mode="HTML")
    
    try:
        prompt = f"Придумай романтическую карту сокровищ для свидания дома из {rooms} комнат. У пары увлечения: {user[5]}, любимый фильм: {user[6]}. Опиши пошагово 4 локации (где искать записки) и финальный сюрприз. Формат: красивый список с эмодзи."
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8
        )
        text = response.choices[0].message.content
        await wait_msg.edit_text(
            f"🗺️ <b>Ваша карта сокровищ готова!</b>\n\n{text}\n\n"
            "Устройте незабываемый вечер 💕",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"AI ERROR: {e}")
        await wait_msg.edit_text(
            "😔 Не получилось создать карту. Попробуйте позже.",
            parse_mode="HTML"
        )
    
    await state.clear()

# --- Настройки ---
@dp.message(Command("settings"))
@dp.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: types.Message):
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
async def cmd_subscribe(message: types.Message):
    user_id = message.from_user.id
    days = database.days_left(user_id)
    user = database.get_user(user_id)
    
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