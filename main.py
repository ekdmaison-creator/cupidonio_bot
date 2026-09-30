import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import ai_engine
import database
from config import (ACHIEVEMENTS, ADMIN_ID, BOT_TOKEN, MAX_TASKS_PER_DAY, PAY_BANK, PAY_NAME, PAY_PHONE,
                    PLANS, REFERRAL_BONUS_DAYS, TRIAL_DAYS, TZ, date_events, esc, format_ai, get_level,
                    next_milestone, now, parse_date, parse_day_month, plural, progress_bar, today_str)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cupidonio")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone=TZ, job_defaults={"misfire_grace_time": 3600, "coalesce": True})

# ============ СОСТОЯНИЯ (FSM) ============


class Reg(StatesGroup):
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
    birthday = State()


class TreasureForm(StatesGroup):
    rooms = State()


class FeedbackForm(StatesGroup):
    waiting = State()


class DiaryForm(StatesGroup):
    text = State()


class StateForm(StatesGroup):
    choose = State()


class BirthdayForm(StatesGroup):
    value = State()


class PayForm(StatesGroup):
    receipt = State()


# ============ КНОПКИ И КЛАВИАТУРЫ ============

B_TASK, B_DONE = "📝 Задание", "✅ Выполнено"
B_DATE, B_MSG = "🌹 Свидание", "💌 Написать партнёру"
B_DIARY, B_PROGRESS = "📖 Дневник", "📊 Прогресс"
B_TREASURE, B_STATE = "🗺️ Карта сокровищ", "💭 Состояние"
B_SUB, B_MORE = "💎 Подписка", "⚙️ Ещё"
B_DAILY, B_3W = "📅 Каждый день", "🗓 3 раза в неделю"
B_BDAY, B_INVITE = "🎂 День рождения партнёра", "🎁 Пригласить друга"
B_BACK, B_SKIP = "⬅️ Назад в меню", "⏭ Пропустить"

STATE_MAP = {"💚 Всё отлично": "отлично", "💛 Небольшие трудности": "небольшие трудности",
             "🧡 Отдалились": "отдалились", "❤️‍🩹 Кризис": "кризис"}
STATE_REPLY = {
    "отлично": "🔥 Отлично! Буду давать смелые и тёплые задания.",
    "небольшие трудности": "💛 Понял. Сделаю задания мягче: вернём тепло потихоньку, без давления.",
    "отдалились": "🧡 Понял тебя. Буду аккуратным: маленькие шаги, чтобы снова почувствовать друг друга.",
    "кризис": "❤️‍🩹 Понял. Задания будут очень мягкими: просто забота и тепло, без романтики через силу.",
}
LOVE = ["💬 Слова", "⏰ Время", "🎁 Подарки", "🤝 Помощь", "🤗 Прикосновения"]
GENDERS = {"👨 Мужчина": "мужчина", "👩 Женщина": "женщина"}


def rkb(*rows):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows],
                               resize_keyboard=True)


def ikb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])


main_menu = rkb([B_TASK, B_DONE], [B_DATE, B_MSG], [B_DIARY, B_PROGRESS],
                [B_TREASURE, B_STATE], [B_SUB, B_MORE])
gender_kb = rkb(list(GENDERS))
love_kb = rkb(LOVE[:2], LOVE[2:4], LOVE[4:])
state_kb = rkb(*[[t] for t in STATE_MAP])
skip_kb = rkb([B_SKIP])
more_kb = rkb([B_DAILY, B_3W], [B_BDAY, B_INVITE], [B_BACK])

mood_kb = ikb([("😍 Всё прекрасно", "mood_great")], [("🙂 Хорошо", "mood_good")],
              [("😐 Есть трудности", "mood_hard")], [("😔 Сложный период", "mood_crisis")])


def task_kb(later=True):
    rows = [[("✅ Выполнено", "task_done")],
            [("⚡ Проще", "task_easy"), ("🔥 Смелее", "task_bold")],
            [("🔄 Другое", "task_new"), ("❌ Не понравилось", "task_dislike")]]
    if later:
        rows.append([("⏰ Напомнить позже", "task_later")])
    return ikb(*rows)


reaction_kb = ikb([("😍 В восторге", "react_love"), ("🙂 Нормально", "react_ok"), ("😕 Не зашло", "react_meh")],
                  [("📖 Записать момент", "diary_add")])
diary_only_kb = ikb([("📖 Записать момент", "diary_add")])
dislike_kb = ikb([("Банально", "fb_banal"), ("Не про нас", "fb_notus")],
                 [("Слишком сложно", "fb_hard"), ("Напишу сам(а)", "fb_custom")])
FB_TEXT = {"fb_banal": "слишком банальное, хочу оригинальнее", "fb_notus": "задание не подходит нашей паре",
           "fb_hard": "слишком сложно, хочу проще и быстрее"}


def date_menu_kb():
    return ikb(*[[(t, f"date_{k}")] for k, (t, _) in ai_engine.DATE_OPTIONS.items()])


def msg_menu_kb():
    return ikb(*[[(t, f"msg_{k}")] for k, (t, _) in ai_engine.MESSAGE_KINDS.items()])


def plans_kb():
    rows = []
    base = PLANS["1m"]["price"]
    for key, p in PLANS.items():
        months = int(key[:-1])
        save = round(100 - p["price"] * 100 / (base * months))
        label = f"{p['title']} — {p['price']} ₽" + (f" (−{save}%)" if save > 0 else "")
        rows.append([(label, f"buy_{key}")])
    return ikb(*rows)


# ============ ХЕЛПЕРЫ ============

def clip(text, n=200):
    return (text or "").strip()[:n]


async def safe_send(uid, text, **kw) -> bool:
    try:
        await bot.send_message(uid, text, **kw)
        return True
    except TelegramForbiddenError:
        log.info("Пользователь %s заблокировал бота", uid)
    except Exception as e:
        log.warning("Не удалось отправить %s: %s", uid, e)
    return False


async def edit(cb: CallbackQuery, text, reply_markup=None):
    try:
        await cb.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            await bot.send_message(cb.from_user.id, text, reply_markup=reply_markup)


async def run_ai(fn, *args):
    try:
        return await fn(*args)
    except Exception:
        log.exception("Ошибка ИИ")
        return None


def sub_until(uid):
    end = database.get_sub_end(uid)
    return datetime.strptime(end, "%Y-%m-%d").strftime("%d.%m.%Y") if end else "—"


def paywall_text(uid, kind="expired"):
    week, month, streak, total = database.get_stats(uid)
    moments = database.count_moments(uid)
    head = ("⏳ <b>Завтра заканчивается ваш доступ</b>" if kind == "tomorrow"
            else "🔒 <b>Доступ к заданиям закончился</b>")
    if total or moments:
        body = (f"За это время вы вместе:\n"
                f"• выполнили заданий: <b>{total}</b>\n"
                f"• лучшая текущая серия: <b>{streak}</b> {plural(streak, 'день', 'дня', 'дней')}\n"
                f"• сохранили моментов в дневнике: <b>{moments}</b>\n\n"
                "Ваш прогресс и дневник сохранены, они никуда не денутся 💕")
    else:
        body = "Вы ещё не успели попробовать всё: задания, свидания, готовые сообщения и дневник пары."
    return (f"{head}\n\n{body}\n\nПродолжим? Выберите тариф 👇\n"
            f"<i>Один месяц стоит меньше, чем один ужин в кафе.</i>")


def task_message(text, title="📝 Ваше задание на сегодня"):
    return f"<b>{title}</b>\n\n{format_ai(text)}\n\n<i>Когда выполните, нажмите «Выполнено» 👇</i>"


ERRORS = {
    "limit": f"🙈 На сегодня лимит заданий исчерпан ({MAX_TASKS_PER_DAY}). Возвращайтесь завтра ✨",
    "error": "😔 <b>Не получилось создать задание.</b>\n\nПопробуйте ещё раз через минуту. "
             "Текущее задание сохранено: откройте его кнопкой «📝 Задание».",
}
AI_FAIL = "😔 Не получилось. Попробуйте ещё раз через минуту."


async def build_task(uid, mode=None):
    """Возвращает (текст, код_ошибки)."""
    if database.count_today_tasks(uid) >= MAX_TASKS_PER_DAY:
        return None, "limit"
    res = await run_ai(ai_engine.generate_task, uid, mode)
    if not res:
        return None, "error"
    text, category = res
    database.save_task(uid, text, category)
    return text, ""


def finish_task(uid):
    if not database.mark_done(uid):
        return None
    week, month, streak, total = database.get_stats(uid)
    lvl, start, nxt = get_level(total)
    lines = ["🎉 <b>Задание выполнено!</b>", "",
             f"🔥 Серия: <b>{streak}</b> {plural(streak, 'день', 'дня', 'дней')} подряд"]
    if nxt:
        lines.append(f"{lvl}\n{progress_bar(total, start, nxt)} {total}/{nxt}")
    else:
        lines.append(f"{lvl}: максимальный уровень!")
    if lvl != get_level(total - 1)[0]:
        lines.append(f"\n🎊 <b>Новый уровень: {lvl}</b>")
    for code in database.check_achievements(uid):
        icon, title, desc = ACHIEVEMENTS[code]
        lines.append(f"\n🏆 <b>Достижение: {icon} {title}</b>: {desc}")
    lines.append("\nКак прошло? Оценка помогает мне подбирать лучше 👇")
    return "\n".join(lines)


async def notify_achievements(uid, codes):
    for code in codes:
        icon, title, desc = ACHIEVEMENTS[code]
        await safe_send(uid, f"🏆 <b>Новое достижение!</b>\n{icon} <b>{title}</b>: {desc}")


def progress_text(uid):
    u = database.get_user(uid)
    week, month, streak, total = database.get_stats(uid)
    lvl, start, nxt = get_level(total)
    bar = progress_bar(total, start, nxt) if nxt else progress_bar(1, 0, 1)
    nxt_txt = f"{total}/{nxt}" if nxt else "максимум"
    unlocked = database.get_achievements(uid)
    ach = "\n".join(f"{icon if code in unlocked else '🔒'} {title}: {desc}"
                    for code, (icon, title, desc) in ACHIEVEMENTS.items())
    text = (f"📊 <b>Ваш прогресс</b>\n━━━━━━━━━━━━━━━\n"
            f"{lvl}\n{bar} {nxt_txt}\n\n"
            f"✅ За неделю: <b>{week}</b>\n✅ За месяц: <b>{month}</b>\n"
            f"🏆 Всего дней с заданиями: <b>{total}</b>\n"
            f"🔥 Серия: <b>{streak}</b> {plural(streak, 'день', 'дня', 'дней')}\n"
            f"📖 Моментов в дневнике: <b>{database.count_moments(uid)}</b>\n━━━━━━━━━━━━━━━\n\n"
            f"<b>Достижения</b>\n{ach}")
    meet = parse_date(u["meeting_date"])
    if meet:
        label, n = next_milestone(meet.date(), now().date())
        text += (f"\n\n🎉 Ближайшая красивая дата: <b>{label}</b>, "
                 f"через {n} {plural(n, 'день', 'дня', 'дней')}")
    return text


async def need_user(message: Message):
    u = database.get_user(message.from_user.id)
    if not u:
        await message.answer("Сначала познакомимся: нажмите /start")
    return u


async def need_access(message: Message) -> bool:
    if not await need_user(message):
        return False
    uid = message.from_user.id
    if database.is_active(uid):
        return True
    await message.answer(paywall_text(uid), reply_markup=plans_kb())
    return False


async def cb_access(cb: CallbackQuery) -> bool:
    uid = cb.from_user.id
    if not database.get_user(uid):
        await cb.answer("Сначала нажмите /start", show_alert=True)
        return False
    if database.is_active(uid):
        return True
    await cb.answer()
    await bot.send_message(uid, paywall_text(uid), reply_markup=plans_kb())
    return False


async def reset(message: Message, state: FSMContext):
    """Сбрасывает диалог, но только у зарегистрированных (чтобы не ломать регистрацию)."""
    if database.get_user(message.from_user.id):
        await state.clear()


# ============ /start И РЕГИСТРАЦИЯ ============

@dp.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    uid = message.from_user.id
    u = database.get_user(uid)
    if u:
        await state.clear()
        week, month, streak, total = database.get_stats(uid)
        status = (f"✅ Доступ открыт до <b>{sub_until(uid)}</b>" if database.is_active(uid)
                  else "🔒 Нужна подписка: кнопка «💎 Подписка»")
        await message.answer(f"💕 <b>С возвращением, {esc(u['name'])}!</b>\n\n{status}\n"
                             f"🔥 Серия: <b>{streak}</b> {plural(streak, 'день', 'дня', 'дней')}\n\n"
                             "Выберите действие на кнопках ниже 👇", reply_markup=main_menu)
        return
    ref = 0
    arg = command.args or ""
    if arg.startswith("ref_") and arg[4:].isdigit() and int(arg[4:]) != uid:
        ref = int(arg[4:])
    await state.clear()
    await state.update_data(ref=ref)
    bonus = f"\n🎁 Вы пришли по приглашению: получите <b>+{REFERRAL_BONUS_DAYS} дней</b> в подарок!\n" if ref else ""
    await message.answer(
        "💕 <b>Добро пожаловать в Cupidonio!</b>\n\n"
        "Я помогаю парам сохранять тепло: каждый день даю маленькое, живое задание именно для вас.\n\n"
        "✨ Персональные задания на 5–15 минут\n🌹 Идеи свиданий под ваш бюджет\n"
        "💌 Готовые сообщения любимому человеку\n📖 Дневник ваших моментов\n"
        "🏆 Уровни и достижения\n\n"
        f"🎁 <b>Первые {TRIAL_DAYS} дня бесплатно</b>{bonus}\n"
        "Давайте познакомимся. <b>Как тебя зовут?</b>", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Reg.name)


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer("ℹ️ <b>Как это работает</b>\n\n"
                         "📝 Задание: мини-квест на сегодня\n🌹 Свидание: идея под ваш бюджет\n"
                         "💌 Сообщение: готовый текст для партнёра\n📖 Дневник: ваши моменты\n"
                         "📊 Прогресс: уровень, серия, достижения\n💭 Состояние: подстроить тон заданий\n\n"
                         "Команды: /task /done /stats /treasure /state /settings /subscribe /invite")


@dp.message(Reg.name, F.text)
async def reg_name(message: Message, state: FSMContext):
    await state.update_data(name=clip(message.text, 40))
    await message.answer("Приятно познакомиться! 👋\n\nУкажи свой пол, чтобы я давал правильные задания:",
                         reply_markup=gender_kb)
    await state.set_state(Reg.user_gender)


@dp.message(Reg.user_gender, F.text)
async def reg_user_gender(message: Message, state: FSMContext):
    if message.text not in GENDERS:
        await message.answer("Пожалуйста, выбери из кнопок ниже 👇", reply_markup=gender_kb)
        return
    await state.update_data(user_gender=GENDERS[message.text])
    await message.answer("Отлично! 💑\n\nКак зовут твоего партнёра?", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Reg.partner_name)


@dp.message(Reg.partner_name, F.text)
async def reg_partner_name(message: Message, state: FSMContext):
    await state.update_data(partner_name=clip(message.text, 40))
    await message.answer("Какой пол у твоего партнёра?", reply_markup=gender_kb)
    await state.set_state(Reg.partner_gender)


@dp.message(Reg.partner_gender, F.text)
async def reg_partner_gender(message: Message, state: FSMContext):
    if message.text not in GENDERS:
        await message.answer("Пожалуйста, выбери из кнопок ниже 👇", reply_markup=gender_kb)
        return
    await state.update_data(partner_gender=GENDERS[message.text])
    await message.answer("📅 Введите дату вашего знакомства в формате <b>ДД.ММ.ГГГГ</b>\n\n"
                         "Например: <i>15.05.2020</i>", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Reg.meeting_date)


@dp.message(Reg.meeting_date, F.text)
async def reg_date(message: Message, state: FSMContext):
    d = parse_date(message.text)
    if not d:
        await message.answer("Не получилось распознать дату 🤔\nНапишите в формате <b>ДД.ММ.ГГГГ</b>, "
                             "например <i>15.05.2020</i> (дата не может быть в будущем).")
        return
    await state.update_data(meeting_date=d.strftime("%d.%m.%Y"))
    await message.answer("📍 Где вы познакомились?\n\nНапример: <i>кафе «Уют», университет, через друзей</i>")
    await state.set_state(Reg.meeting_place)


@dp.message(Reg.meeting_place, F.text)
async def reg_place(message: Message, state: FSMContext):
    await state.update_data(meeting_place=clip(message.text))
    await message.answer("🎯 Расскажите про ваши общие увлечения (через запятую).\n\n"
                         "Например: <i>путешествия, кино, кулинария, йога</i>")
    await state.set_state(Reg.hobbies)


@dp.message(Reg.hobbies, F.text)
async def reg_hobbies(message: Message, state: FSMContext):
    await state.update_data(hobbies=clip(message.text))
    await message.answer("🎬 Какой ваш общий любимый фильм или книга?")
    await state.set_state(Reg.favorite_movie)


@dp.message(Reg.favorite_movie, F.text)
async def reg_movie(message: Message, state: FSMContext):
    await state.update_data(favorite_movie=clip(message.text))
    await message.answer("💖 Какой у вас главный язык любви?\n\nВыберите из кнопок ниже 👇", reply_markup=love_kb)
    await state.set_state(Reg.love_language)


@dp.message(Reg.love_language, F.text)
async def reg_love(message: Message, state: FSMContext):
    if message.text not in LOVE:
        await message.answer("Пожалуйста, выбери из кнопок ниже 👇", reply_markup=love_kb)
        return
    await state.update_data(love_language=message.text)
    await message.answer("Как сейчас обстоят дела в ваших отношениях? 💭\n\n"
                         "Это поможет подобрать правильный тон заданий:", reply_markup=state_kb)
    await state.set_state(Reg.relationship_state)


@dp.message(Reg.relationship_state, F.text)
async def reg_state(message: Message, state: FSMContext):
    if message.text not in STATE_MAP:
        await message.answer("Пожалуйста, выбери из кнопок ниже 👇", reply_markup=state_kb)
        return
    await state.update_data(relationship_state=STATE_MAP[message.text])
    await message.answer("🎂 Когда день рождения у партнёра? Формат <b>ДД.ММ</b>, например <i>07.03</i>.\n\n"
                         "За 3 дня до даты я подскажу идею сюрприза. Можно пропустить.", reply_markup=skip_kb)
    await state.set_state(Reg.birthday)


@dp.message(Reg.birthday, F.text)
async def reg_birthday(message: Message, state: FSMContext):
    birthday = ""
    if message.text != B_SKIP:
        birthday = parse_day_month(message.text)
        if not birthday:
            await message.answer("Формат <b>ДД.ММ</b>, например <i>07.03</i>. Или нажмите «Пропустить».",
                                 reply_markup=skip_kb)
            return
    data = await state.get_data()
    uid = message.from_user.id
    ref = data.get("ref", 0)
    database.add_user(uid, data, username=message.from_user.username or "", referred_by=ref)
    if birthday:
        database.set_birthday(uid, birthday)
    await state.clear()

    bonus = ""
    if ref and database.get_user(ref) and database.add_referral(ref, uid):
        database.extend_subscription(uid, REFERRAL_BONUS_DAYS)
        database.extend_subscription(ref, REFERRAL_BONUS_DAYS)
        bonus = f"\n🎁 Бонус за приглашение: <b>+{REFERRAL_BONUS_DAYS} дней</b>!"
        await safe_send(ref, f"🎉 По вашей ссылке пришёл новый пользователь!\n"
                             f"Вам начислено <b>+{REFERRAL_BONUS_DAYS} дней</b> подписки 💕")
        await notify_achievements(ref, database.check_achievements(ref))

    await message.answer(f"🎉 <b>Отлично, {esc(data['name'])}!</b>\n\nВы зарегистрированы. "
                         f"Доступ открыт до <b>{sub_until(uid)}</b>.{bonus}\n\n"
                         "Каждое утро в 9:00 я буду присылать задание. А первое получите прямо сейчас 👇",
                         reply_markup=main_menu)
    wait = await message.answer("✨ <i>Создаю ваше первое задание…</i>")
    text, err = await build_task(uid)
    if err:
        await wait.edit_text(ERRORS[err])
        return
    await wait.edit_text(task_message(text, "🎁 Ваше первое задание"), reply_markup=task_kb())


# ============ ЗАДАНИЯ ============

@dp.message(Command("task"))
@dp.message(F.text == B_TASK)
async def cmd_task(message: Message, state: FSMContext):
    if not await need_access(message):
        return
    await reset(message, state)
    uid = message.from_user.id
    today = database.get_today_task(uid)
    if today:
        text, completed = today
        if completed:
            await message.answer("✅ <b>Сегодняшнее задание уже выполнено!</b>\n\nНовое придёт завтра утром 🌅\n"
                                 "А пока можно выбрать идею свидания 🌹 или написать партнёру 💌")
        else:
            await message.answer(task_message(text), reply_markup=task_kb())
        return
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    wait = await message.answer("✨ <i>Создаю для вас особенное задание…</i>")
    text, err = await build_task(uid)
    if err:
        await wait.edit_text(ERRORS[err])
        return
    await wait.edit_text(task_message(text), reply_markup=task_kb())


async def regenerate(cb: CallbackQuery, mode, title):
    if not await cb_access(cb):
        return
    uid = cb.from_user.id
    today = database.get_today_task(uid)
    if today and today[1] == 1:
        await cb.answer("Сегодняшнее задание уже выполнено ✅", show_alert=True)
        return
    await cb.answer()
    await edit(cb, "✨ <i>Подбираю другой вариант…</i>")
    text, err = await build_task(uid, mode)
    if err:
        await edit(cb, ERRORS[err])
        return
    await edit(cb, task_message(text, title), task_kb())


@dp.callback_query(F.data == "task_new")
async def cb_new(cb: CallbackQuery):
    await regenerate(cb, None, "🔄 Новое задание")


@dp.callback_query(F.data == "task_easy")
async def cb_easy(cb: CallbackQuery):
    await regenerate(cb, "easy", "⚡ Упрощённый вариант")


@dp.callback_query(F.data == "task_bold")
async def cb_bold(cb: CallbackQuery):
    await regenerate(cb, "bold", "🔥 Смелый вариант")


@dp.callback_query(F.data == "task_later")
async def cb_later(cb: CallbackQuery):
    await cb.answer("Ок! Напомню вечером в 19:00 ⏰")
    await cb.message.edit_reply_markup(reply_markup=task_kb(later=False))


@dp.callback_query(F.data == "task_done")
async def cb_done(cb: CallbackQuery):
    uid = cb.from_user.id
    today = database.get_today_task(uid)
    if not today:
        await cb.answer("Задания на сегодня нет.", show_alert=True)
        return
    if today[1] == 1:
        await cb.answer("Уже выполнено ✅")
        return
    text = finish_task(uid)
    await cb.answer("Готово! 🎉")
    await edit(cb, text or "✅ Выполнено!", reaction_kb)


@dp.message(Command("done"))
@dp.message(F.text == B_DONE)
async def cmd_done(message: Message, state: FSMContext):
    if not await need_access(message):
        return
    await reset(message, state)
    today = database.get_today_task(message.from_user.id)
    if not today:
        await message.answer("❌ Сегодня задания ещё не было. Нажмите <b>📝 Задание</b>.")
    elif today[1] == 1:
        await message.answer("ℹ️ Это задание уже отмечено выполненным. Возвращайтесь завтра 🌅")
    else:
        await message.answer(finish_task(message.from_user.id), reply_markup=reaction_kb)


@dp.callback_query(F.data.in_({"react_love", "react_ok", "react_meh"}))
async def cb_reaction(cb: CallbackQuery):
    reaction = cb.data.split("_")[1]
    database.set_reaction(cb.from_user.id, reaction)
    answers = {"love": "Запомнил! Буду искать похожее 💕", "ok": "Принято 👍", "meh": "Понял, учту 🙏"}
    await cb.answer(answers[reaction])
    await cb.message.edit_reply_markup(reply_markup=diary_only_kb)


@dp.callback_query(F.data == "task_dislike")
async def cb_dislike(cb: CallbackQuery):
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(cb.from_user.id, "🤔 <b>Спасибо за честность!</b> Что не так?", reply_markup=dislike_kb)


@dp.callback_query(F.data.in_(set(FB_TEXT)))
async def cb_quick_feedback(cb: CallbackQuery):
    uid = cb.from_user.id
    today = database.get_today_task(uid)
    database.save_feedback(uid, today[0] if today else "—", FB_TEXT[cb.data])
    await cb.answer("Записал!")
    await edit(cb, "💾 <b>Записал!</b> Буду учитывать это в новых заданиях.",
               ikb([("🔄 Дай другое задание", "task_new")]))


@dp.callback_query(F.data == "fb_custom")
async def cb_custom_feedback(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await edit(cb, "✍️ Напишите одним сообщением, что изменить. Например: «больше игры» или «без готовки».")
    await state.set_state(FeedbackForm.waiting)


@dp.message(FeedbackForm.waiting, F.text)
async def process_feedback(message: Message, state: FSMContext):
    uid = message.from_user.id
    today = database.get_today_task(uid)
    database.save_feedback(uid, today[0] if today else "—", clip(message.text, 300))
    await state.clear()
    await message.answer("💾 <b>Записал!</b> Буду учитывать это. Хотите другое задание?",
                         reply_markup=ikb([("🔄 Дай другое задание", "task_new")]))


@dp.callback_query(F.data.startswith("mood_"))
async def cb_mood(cb: CallbackQuery):
    mapping = {"mood_great": ("great", "😍", "отлично"), "mood_good": ("good", "🙂", "отлично"),
               "mood_hard": ("hard", "😐", "небольшие трудности"), "mood_crisis": ("crisis", "😔", "кризис")}
    if cb.data not in mapping:
        await cb.answer()
        return
    key, emoji, new_state = mapping[cb.data]
    database.update_checkin(cb.from_user.id, key)
    database.update_relationship_state(cb.from_user.id, new_state)
    await cb.answer()
    await edit(cb, f"{emoji} <b>Спасибо, что поделились.</b>\n\n{STATE_REPLY[new_state]}")


# ============ СВИДАНИЯ, СООБЩЕНИЯ ============

DATE_INTRO = "🌹 <b>Генератор свиданий</b>\n\nКакой формат хотите? Идея подстроится под вашу пару 👇"
MSG_INTRO = "💌 <b>Написать партнёру</b>\n\nВыберите повод, и я подготовлю сообщение именно от вас 👇"


@dp.message(Command("date"))
@dp.message(F.text == B_DATE)
async def cmd_date(message: Message, state: FSMContext):
    if not await need_access(message):
        return
    await reset(message, state)
    await message.answer(DATE_INTRO, reply_markup=date_menu_kb())


@dp.callback_query(F.data.startswith("date_"))
async def cb_date(cb: CallbackQuery):
    if not await cb_access(cb):
        return
    key = cb.data[5:]
    await cb.answer()
    if key == "menu":
        await edit(cb, DATE_INTRO, date_menu_kb())
        return
    if key not in ai_engine.DATE_OPTIONS:
        return
    await edit(cb, "✨ <i>Придумываю свидание…</i>")
    text = await run_ai(ai_engine.generate_date, cb.from_user.id, key)
    if not text:
        await edit(cb, AI_FAIL, date_menu_kb())
        return
    await edit(cb, f"{ai_engine.DATE_OPTIONS[key][0]}\n\n{format_ai(text)}",
               ikb([("🔄 Другая идея", f"date_{key}")], [("🌹 Другой формат", "date_menu")]))


@dp.message(Command("write"))
@dp.message(F.text == B_MSG)
async def cmd_msg(message: Message, state: FSMContext):
    if not await need_access(message):
        return
    await reset(message, state)
    await message.answer(MSG_INTRO, reply_markup=msg_menu_kb())


@dp.callback_query(F.data.startswith("msg_"))
async def cb_msg(cb: CallbackQuery):
    if not await cb_access(cb):
        return
    key = cb.data[4:]
    await cb.answer()
    if key == "menu":
        await edit(cb, MSG_INTRO, msg_menu_kb())
        return
    if key not in ai_engine.MESSAGE_KINDS:
        return
    await edit(cb, "✨ <i>Подбираю слова…</i>")
    text = await run_ai(ai_engine.generate_message, cb.from_user.id, key)
    if not text:
        await edit(cb, AI_FAIL, msg_menu_kb())
        return
    await edit(cb, f"{ai_engine.MESSAGE_KINDS[key][0]}\n\n<blockquote>{esc(text)}</blockquote>\n\n"
                   "<i>Скопируйте, при желании поправьте под себя и отправьте 💕</i>",
               ikb([("🔄 Другой вариант", f"msg_{key}")], [("💌 Другой повод", "msg_menu")]))


# ============ ДНЕВНИК ============

@dp.message(Command("diary"))
@dp.message(F.text == B_DIARY)
async def cmd_diary(message: Message, state: FSMContext):
    if not await need_user(message):
        return
    await reset(message, state)
    uid = message.from_user.id
    total = database.count_moments(uid)
    if total == 0:
        await message.answer("📖 <b>Дневник вашей пары</b>\n\nЗдесь живут ваши самые тёплые моменты: "
                             "смешные фразы, первые разы, неожиданные мелочи. Через год будет приятно перечитать.\n\n"
                             "Запишите первый момент 👇", reply_markup=diary_only_kb)
        return
    rows = database.get_moments(uid, 5)
    body = "\n\n".join(f"📅 <i>{r['created_at']}</i>\n{esc(r['text'])}" for r in rows)
    await message.answer(f"📖 <b>Дневник вашей пары</b>\nЗаписей: <b>{total}</b>\n\n{body}",
                         reply_markup=diary_only_kb)


@dp.callback_query(F.data == "diary_add")
async def cb_diary_add(cb: CallbackQuery, state: FSMContext):
    if not await cb_access(cb):
        return
    await cb.answer()
    await bot.send_message(cb.from_user.id, "✍️ Напишите момент одним сообщением: что запомнилось сегодня?",
                           reply_markup=ReplyKeyboardRemove())
    await state.set_state(DiaryForm.text)


@dp.message(DiaryForm.text, F.text)
async def diary_save(message: Message, state: FSMContext):
    uid = message.from_user.id
    database.add_moment(uid, clip(message.text, 500))
    await state.clear()
    await message.answer("📖 <b>Сохранено в дневнике!</b> Эти моменты останутся с вами 💕", reply_markup=main_menu)
    await notify_achievements(uid, database.check_achievements(uid))


# ============ ПРОГРЕСС ============

@dp.message(Command("stats"))
@dp.message(F.text.in_([B_PROGRESS, "📊 Статистика"]))
async def cmd_stats(message: Message, state: FSMContext):
    if not await need_user(message):
        return
    await reset(message, state)
    await message.answer(progress_text(message.from_user.id), reply_markup=main_menu)


# ============ КАРТА СОКРОВИЩ ============

@dp.message(Command("treasure"))
@dp.message(F.text == B_TREASURE)
async def cmd_treasure(message: Message, state: FSMContext):
    if not await need_access(message):
        return
    await reset(message, state)
    await message.answer("🗺️ <b>Карта сокровищ</b>\n━━━━━━━━━━━━━━━\n\n"
                         "Я создам квест по вашей квартире: записки-загадки и финальный сюрприз.\n\n"
                         "🏠 <b>Сколько комнат в вашей квартире?</b> Напишите цифру.")
    await state.set_state(TreasureForm.rooms)


@dp.message(TreasureForm.rooms, F.text)
async def treasure_rooms(message: Message, state: FSMContext):
    digits = "".join(ch for ch in message.text if ch.isdigit())
    if not digits or not 1 <= int(digits) <= 15:
        await message.answer("Напишите число комнат цифрой, например: 2")
        return
    await state.clear()
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    wait = await message.answer("✨ <i>Придумываю маршрут…</i>")
    text = await run_ai(ai_engine.generate_treasure, message.from_user.id, int(digits))
    if not text:
        await wait.edit_text(AI_FAIL)
        return
    await wait.edit_text(f"🗺️ <b>Ваша карта сокровищ готова!</b>\n━━━━━━━━━━━━━━━\n\n{format_ai(text)}\n\n"
                         "━━━━━━━━━━━━━━━\nУстройте незабываемый вечер 💕")


# ============ СОСТОЯНИЕ ОТНОШЕНИЙ ============

@dp.message(Command("state"))
@dp.message(F.text == B_STATE)
async def cmd_state(message: Message, state: FSMContext):
    if not await need_user(message):
        return
    await reset(message, state)
    current = database.get_relationship_state(message.from_user.id)
    await message.answer(f"💭 <b>Как сейчас дела в отношениях?</b>\n\nТекущий статус: <b>{esc(current)}</b>\n\n"
                         "Выберите актуальный, и я подстрою тон заданий 👇", reply_markup=state_kb)
    await state.set_state(StateForm.choose)


@dp.message(StateForm.choose, F.text)
async def state_choose(message: Message, state: FSMContext):
    if message.text not in STATE_MAP:
        await message.answer("Пожалуйста, выберите вариант из кнопок 👇", reply_markup=state_kb)
        return
    new_state = STATE_MAP[message.text]
    database.update_relationship_state(message.from_user.id, new_state)
    await state.clear()
    await message.answer(f"{STATE_REPLY[new_state]}\n\nГлавное меню 👇", reply_markup=main_menu)


# ============ ЕЩЁ: НАСТРОЙКИ, ДР, ПРИГЛАШЕНИЯ ============

@dp.message(Command("settings"))
@dp.message(F.text == B_MORE)
async def cmd_more(message: Message, state: FSMContext):
    if not await need_user(message):
        return
    await reset(message, state)
    u = database.get_user(message.from_user.id)
    freq = "📅 каждый день" if database.get_setting(message.from_user.id) == "daily" else "🗓 3 раза в неделю"
    bday = u.get("partner_birthday") or "не указан"
    await message.answer(f"⚙️ <b>Настройки</b>\n\nЧастота заданий: <b>{freq}</b>\n"
                         f"День рождения партнёра: <b>{esc(bday)}</b>\n\nВыберите действие 👇",
                         reply_markup=more_kb)


@dp.message(F.text.in_([B_DAILY, B_3W]))
async def set_frequency(message: Message):
    if not await need_user(message):
        return
    if message.text == B_DAILY:
        database.update_setting(message.from_user.id, "daily")
        await message.answer("✅ Готово! Задания будут приходить каждый день.", reply_markup=main_menu)
    else:
        database.update_setting(message.from_user.id, "3times_week")
        await message.answer("✅ Готово! Задания будут приходить по понедельникам, средам и пятницам.",
                             reply_markup=main_menu)


@dp.message(F.text == B_BACK)
async def back_to_menu(message: Message, state: FSMContext):
    await reset(message, state)
    await message.answer("Главное меню 👇", reply_markup=main_menu)


@dp.message(F.text == B_BDAY)
async def ask_birthday(message: Message, state: FSMContext):
    if not await need_user(message):
        return
    await state.set_state(BirthdayForm.value)
    await message.answer("🎂 Напишите день рождения партнёра в формате <b>ДД.ММ</b>, например <i>07.03</i>.\n"
                         "За 3 дня до даты я подскажу идею сюрприза.", reply_markup=rkb([B_BACK]))


@dp.message(BirthdayForm.value, F.text)
async def save_birthday(message: Message, state: FSMContext):
    value = parse_day_month(message.text)
    if not value:
        await message.answer("Формат <b>ДД.ММ</b>, например <i>07.03</i>. Попробуйте ещё раз.")
        return
    database.set_birthday(message.from_user.id, value)
    await state.clear()
    await message.answer(f"✅ Запомнил: <b>{value}</b>. Напомню заранее 🎁", reply_markup=main_menu)


@dp.message(Command("invite"))
@dp.message(F.text == B_INVITE)
async def cmd_invite(message: Message, state: FSMContext):
    if not await need_user(message):
        return
    await reset(message, state)
    uid = message.from_user.id
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{uid}"
    await message.answer(f"🎁 <b>Пригласите друзей</b>\n\nКогда друг зарегистрируется по вашей ссылке, "
                         f"вы оба получите <b>+{REFERRAL_BONUS_DAYS} дней</b> подписки.\n\n"
                         f"Ваша ссылка:\n<code>{link}</code>\n\n"
                         f"Приглашено друзей: <b>{database.count_referrals(uid)}</b>", reply_markup=main_menu)


# ============ ПОДПИСКА И ОПЛАТА ============

@dp.message(Command("subscribe"))
@dp.message(Command("confirm"))
@dp.message(F.text == B_SUB)
async def cmd_subscribe(message: Message, state: FSMContext):
    if not await need_user(message):
        return
    await reset(message, state)
    uid = message.from_user.id
    if database.is_active(uid):
        status = f"✅ Доступ открыт до <b>{sub_until(uid)}</b>\n\n"
    else:
        status = "🔒 Доступ закончился.\n\n"
    await message.answer(f"💎 <b>Подписка Cupidonio</b>\n━━━━━━━━━━━━━━━\n{status}"
                         "Что входит:\n📝 Ежедневные персональные задания\n🌹 Идеи свиданий\n"
                         "💌 Готовые сообщения партнёру\n🗺️ Карты сокровищ\n📖 Дневник, уровни и достижения\n"
                         "🎂 Идеи сюрпризов к датам\n\nВыберите тариф 👇\n"
                         "<i>Если продлить заранее, дни прибавятся к остатку.</i>", reply_markup=plans_kb())


@dp.callback_query(F.data.startswith("buy_"))
async def cb_buy(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    plan = PLANS.get(cb.data[4:])
    if not plan or not database.get_user(uid):
        await cb.answer("Сначала нажмите /start", show_alert=True)
        return
    await cb.answer()
    pid = database.create_payment(uid, cb.data[4:], plan["price"], plan["days"])
    await state.set_state(PayForm.receipt)
    await state.update_data(payment_id=pid)
    await bot.send_message(
        uid, f"💳 <b>Тариф: {plan['title']}, {plan['price']} ₽</b>\n\n"
             f"1️⃣ Переведите <b>{plan['price']} ₽</b> по номеру:\n<code>{esc(PAY_PHONE)}</code>\n"
             f"Банк: {esc(PAY_BANK)}, получатель: {esc(PAY_NAME)}\n\n"
             "2️⃣ Пришлите сюда <b>скриншот чека</b> (фото или файл).\n\n"
             "<i>Я проверю оплату и включу подписку, обычно за несколько минут. "
             "Чтобы отменить, напишите «отмена».</i>", reply_markup=ReplyKeyboardRemove())


@dp.message(PayForm.receipt, F.photo | F.document)
async def pay_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    pid = data.get("payment_id")
    pay = database.get_payment(pid) if pid else None
    if not pay or pay["status"] != "pending":
        await state.clear()
        await message.answer("Заявка не найдена. Откройте «💎 Подписка» и выберите тариф заново.",
                             reply_markup=main_menu)
        return
    if not ADMIN_ID:
        await message.answer("Приём оплаты пока не настроен. Напишите администратору.", reply_markup=main_menu)
        return
    u = database.get_user(message.from_user.id)
    username = f"@{esc(u['username'])}" if u.get("username") else "без username"
    info = (f"💰 <b>Новая оплата #{pid}</b>\n\nПользователь: {esc(u['name'])} ({username})\n"
            f"ID: <code>{message.from_user.id}</code>\nТариф: {PLANS[pay['plan']]['title']}, "
            f"<b>{pay['amount']} ₽</b>")
    await bot.send_message(ADMIN_ID, info, reply_markup=ikb([("✅ Подтвердить", f"pay_ok_{pid}"),
                                                             ("❌ Отклонить", f"pay_no_{pid}")]))
    await bot.copy_message(ADMIN_ID, message.chat.id, message.message_id)
    await state.clear()
    await message.answer("📨 <b>Чек получен!</b>\n\nПроверю оплату и включу подписку. Я напишу вам сразу, как только всё будет готово 💕",
                         reply_markup=main_menu)


@dp.message(PayForm.receipt)
async def pay_receipt_other(message: Message, state: FSMContext):
    if (message.text or "").strip().lower() in ("отмена", "cancel"):
        await state.clear()
        await message.answer("Окей, отменил. Главное меню 👇", reply_markup=main_menu)
        return
    await message.answer("Пришлите, пожалуйста, скриншот чека картинкой или файлом. Или напишите «отмена».")


@dp.callback_query(F.data.startswith("pay_"))
async def cb_pay_admin(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        await cb.answer("Нет доступа", show_alert=True)
        return
    try:
        _, action, pid = cb.data.split("_")
        pid = int(pid)
    except ValueError:
        await cb.answer()
        return
    pay = database.get_payment(pid)
    if not pay:
        await cb.answer("Платёж не найден", show_alert=True)
        return
    status = "approved" if action == "ok" else "rejected"
    if not database.set_payment_status(pid, status):
        await cb.answer("Уже обработано", show_alert=True)
        return
    uid = pay["user_id"]
    if status == "approved":
        database.extend_subscription(uid, pay["days"])
        await safe_send(uid, f"🎉 <b>Оплата подтверждена!</b>\n\nДоступ открыт до <b>{sub_until(uid)}</b>. "
                             "Спасибо, что выбрали Cupidonio 💕", reply_markup=main_menu)
        await cb.message.edit_text(f"✅ Платёж #{pid} подтверждён, подписка у пользователя {uid} включена.")
    else:
        await safe_send(uid, "😔 Не получилось подтвердить оплату. Если вы оплатили, пришлите чек ещё раз "
                             "через «💎 Подписка» или напишите администратору.")
        await cb.message.edit_text(f"❌ Платёж #{pid} отклонён.")
    await cb.answer("Готово")


# ============ АДМИН ============

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    s = database.admin_stats()
    await message.answer(f"👑 <b>Админ-панель</b>\n\nВсего пользователей: <b>{s['total']}</b>\n"
                         f"Новых сегодня: <b>{s['new_today']}</b>\nС активным доступом: <b>{s['active']}</b>\n\n"
                         f"Оплат: <b>{s['payments']}</b> от <b>{s['payers']}</b> человек\n"
                         f"Выручка: <b>{s['revenue']} ₽</b>\n\n"
                         "Выдать дни вручную: <code>/give ID ДНИ</code>")


@dp.message(Command("give"))
async def cmd_give(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
        await message.answer("Формат: <code>/give 123456789 30</code>")
        return
    uid, days = int(parts[0]), int(parts[1])
    if not database.get_user(uid):
        await message.answer("Такого пользователя нет.")
        return
    database.extend_subscription(uid, days)
    await message.answer(f"✅ Пользователю {uid} добавлено {days} дн. Доступ до {sub_until(uid)}")
    await safe_send(uid, f"🎁 Вам добавлено <b>{days} дн.</b> доступа. Теперь он открыт до <b>{sub_until(uid)}</b> 💕")


# ============ ПЛАНИРОВЩИК ============

async def job_daily_tasks():
    for uid in database.get_active_user_ids():
        try:
            if database.get_setting(uid) == "3times_week" and now().weekday() not in (0, 2, 4):
                continue
            if database.count_today_tasks(uid):
                continue
            text, err = await build_task(uid)
            if err:
                continue
            await safe_send(uid, task_message(text, "🌅 Доброе утро! Ваше задание на сегодня"),
                            reply_markup=task_kb())
        except Exception:
            log.exception("Ошибка утренней рассылки для %s", uid)
        await asyncio.sleep(0.3)


async def job_evening():
    for uid in database.get_users_with_pending_task():
        await safe_send(uid, "🌙 <b>Вечерний маячок</b>\n\nЗадание на сегодня ещё ждёт вас. "
                             "Это всего 5–15 минут, а вечер станет теплее 💕\n\n"
                             "Нажмите <b>📝 Задание</b>, чтобы открыть его.")
        await asyncio.sleep(0.3)


async def job_checkin():
    for uid in database.get_active_user_ids():
        if database.days_since_checkin(uid) < 3:
            continue
        await safe_send(uid, "💭 <b>Как у вас сейчас?</b>\n\nИногда полезно остановиться и честно спросить себя: "
                             "как наши отношения? Ответьте одним нажатием, и я подстрою тон заданий 👇",
                        reply_markup=mood_kb)
        await asyncio.sleep(0.3)


async def job_paywall():
    for uid in database.get_expiring_tomorrow_ids():
        if await safe_send(uid, paywall_text(uid, "tomorrow"), reply_markup=plans_kb()):
            database.set_paywall_sent(uid, 1)
        await asyncio.sleep(0.3)
    for uid in database.get_expired_unnotified_ids():
        await safe_send(uid, paywall_text(uid, "expired"), reply_markup=plans_kb())
        database.set_paywall_sent(uid, 2)
        await asyncio.sleep(0.3)


async def job_special_dates():
    """За 3 дня и в сам день: годовщины, красивые даты и день рождения партнёра."""
    for uid in database.get_active_user_ids():
        u = database.get_user(uid)
        if not u:
            continue
        meet = parse_date(u["meeting_date"])
        for days_before in (3, 0):
            target = (now() + timedelta(days=days_before)).date()
            phrases = date_events(meet.date(), target) if meet else []
            bd = u.get("partner_birthday") or ""
            if bd and bd == target.strftime("%d.%m"):
                phrases.append(f"день рождения партнёра ({u['partner_name']})")
            for phrase in phrases:
                text = await run_ai(ai_engine.generate_surprise, uid, phrase, days_before)
                if not text:
                    continue
                when = "Уже сегодня" if days_before == 0 else f"Через {days_before} дня"
                await safe_send(uid, f"🎉 <b>{when} особая дата: {esc(phrase)}!</b>\n\n{format_ai(text)}")
                await asyncio.sleep(0.5)


async def job_weekly():
    for uid in database.get_active_user_ids():
        week, month, streak, total = database.get_stats(uid)
        if week:
            text = (f"📅 <b>Итоги недели</b>\n\nЗа 7 дней вы выполнили заданий: <b>{week}</b>\n"
                    f"🔥 Серия: <b>{streak}</b> {plural(streak, 'день', 'дня', 'дней')}\n\n"
                    "Так держать! Маленькие шаги делают большую разницу 💕")
        else:
            text = ("📅 <b>Итоги недели</b>\n\nНа этой неделе мы почти не виделись 🙈\n"
                    "Это нормально. Начните новую неделю с маленького шага: нажмите <b>📝 Задание</b>.")
        await safe_send(uid, text)
        await asyncio.sleep(0.3)


def setup_jobs():
    scheduler.add_job(job_daily_tasks, "cron", hour=9, minute=0)
    scheduler.add_job(job_special_dates, "cron", hour=10, minute=0)
    scheduler.add_job(job_checkin, "cron", hour=11, minute=0)
    scheduler.add_job(job_paywall, "cron", hour=12, minute=0)
    scheduler.add_job(job_evening, "cron", hour=19, minute=0)
    scheduler.add_job(job_weekly, "cron", day_of_week="sun", hour=18, minute=0)


# ============ ЗАПАСНОЙ ОБРАБОТЧИК ============

@dp.message(StateFilter(None), F.text)
async def fallback(message: Message):
    if database.get_user(message.from_user.id):
        await message.answer("Выберите действие на кнопках внизу 👇 Или нажмите /help", reply_markup=main_menu)
    else:
        await message.answer("Давайте познакомимся: нажмите /start")


# ============ ВЕБ-СЕРВЕР И ЗАПУСК ============

async def health_check(request):
    return web.Response(text="OK")


async def start_web_server():
    import os
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Веб-сервер запущен на порту %s", port)


async def main():
    await start_web_server()
    setup_jobs()
    scheduler.start()
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands([
        BotCommand(command="task", description="📝 Задание на сегодня"),
        BotCommand(command="date", description="🌹 Идея свидания"),
        BotCommand(command="write", description="💌 Написать партнёру"),
        BotCommand(command="diary", description="📖 Дневник пары"),
        BotCommand(command="stats", description="📊 Прогресс"),
        BotCommand(command="treasure", description="🗺️ Карта сокровищ"),
        BotCommand(command="subscribe", description="💎 Подписка"),
        BotCommand(command="invite", description="🎁 Пригласить друга"),
        BotCommand(command="help", description="ℹ️ Помощь"),
    ])
    log.info("🤖 Cupidonio запущен и ждёт сообщений...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())