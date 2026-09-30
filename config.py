import os
import re
import html
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)
PAY_PHONE = os.getenv("PAY_PHONE", "+7 XXX XXX-XX-XX")
PAY_BANK = os.getenv("PAY_BANK", "")
PAY_NAME = os.getenv("PAY_NAME", "")
DB_PATH = os.getenv("DB_PATH", "cupidon.db")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))

if not BOT_TOKEN or not DEEPSEEK_API_KEY:
    raise ValueError("BOT_TOKEN или DEEPSEEK_API_KEY не найдены в .env!")

TRIAL_DAYS = 3
REFERRAL_BONUS_DAYS = 7
MAX_TASKS_PER_DAY = 5  # защита от лишних трат на ИИ

PLANS = {
    "1m": {"title": "1 месяц", "days": 30, "price": 300},
    "3m": {"title": "3 месяца", "days": 90, "price": 750},
    "12m": {"title": "12 месяцев", "days": 365, "price": 2400},
}

LEVELS = [
    (0, "🌱 Росток"),
    (3, "✨ Искра"),
    (10, "🔥 Огонёк"),
    (25, "🕯 Тёплый очаг"),
    (50, "🌹 Пламя"),
    (100, "💎 Вечный огонь"),
]

ACHIEVEMENTS = {
    "first_task": ("🎬", "Первый шаг", "Выполнено первое задание"),
    "streak_3": ("🔥", "Три дня подряд", "Серия из 3 дней"),
    "streak_7": ("⚡", "Неделя заботы", "Серия из 7 дней"),
    "streak_30": ("👑", "Месяц любви", "Серия из 30 дней"),
    "total_10": ("🌟", "Десятка", "10 выполненных заданий"),
    "total_50": ("💫", "Полтинник", "50 выполненных заданий"),
    "first_moment": ("📖", "Хранители моментов", "Первая запись в дневнике"),
    "ambassador": ("💌", "Амбассадор", "Пригласили друзей в Cupidonio"),
}


def now() -> datetime:
    """Текущее время в нужном часовом поясе (без tzinfo, чтобы удобно сравнивать)."""
    return datetime.now(TZ).replace(tzinfo=None)


def today_str() -> str:
    return now().strftime("%Y-%m-%d")


def esc(text) -> str:
    """Экранирует символы < > & — чтобы Telegram не ломался на HTML."""
    return html.escape(str(text or ""), quote=False)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def parse_date(text: str):
    """'15.05.2020' (или 15/05/2020, 15-05-2020) -> datetime. Если неверно — None."""
    t = (text or "").strip().replace("/", ".").replace("-", ".").replace(" ", ".")
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", t)
    if not m:
        return None
    try:
        d = datetime(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None
    if d > now() or d.year < 1950:
        return None
    return d


def parse_day_month(text: str):
    """'07.03' -> '07.03'. Если неверно — None."""
    t = (text or "").strip().replace("/", ".").replace("-", ".").replace(" ", ".")
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", t)
    if not m:
        return None
    try:
        datetime(2024, int(m[2]), int(m[1]))
    except ValueError:
        return None
    return f"{int(m[1]):02d}.{int(m[2]):02d}"


def get_level(total: int):
    """Возвращает (название уровня, порог текущего, порог следующего или None)."""
    current = LEVELS[0]
    nxt = None
    for i, (need, name) in enumerate(LEVELS):
        if total >= need:
            current = (need, name)
            nxt = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    return current[1], current[0], nxt


def progress_bar(value: int, start: int, end: int, size: int = 10) -> str:
    if end <= start:
        return "▰" * size
    filled = int((value - start) / (end - start) * size)
    filled = max(0, min(size, filled))
    return "▰" * filled + "▱" * (size - filled)


def clean_markdown(text: str) -> str:
    text = re.sub(r"^\x23{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"^-{3,}$", "─────────────", text, flags=re.MULTILINE)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


HEADING_EMOJI = r"(?:🎯|🌹|🧺|🕰|✨|📍|🎁|🏁)"


def format_ai(text: str) -> str:
    """Безопасный HTML: экранируем текст ИИ и выделяем жирным строки-заголовки."""
    text = esc(clean_markdown(text))
    return re.sub(rf"^({HEADING_EMOJI}.*)$", r"<b>\1</b>", text, flags=re.MULTILINE)


def _safe_date(y: int, m: int, d: int) -> date:
    try:
        return date(y, m, d)
    except ValueError:  # 29 февраля
        return date(y, 3, 1)


def date_events(meet: date, on_date: date) -> list:
    """Какие красивые даты у пары в указанный день."""
    d = (on_date - meet).days
    if d <= 0:
        return []
    if on_date.month == meet.month and on_date.day == meet.day:
        y = on_date.year - meet.year
        return [f"{y} {plural(y, 'год', 'года', 'лет')} вместе"]
    if d % 100 == 0:
        return [f"{d} дней вместе"]
    return []


def next_milestone(meet: date, today: date):
    """Ближайшая красивая дата: (подпись, через сколько дней)."""
    d = (today - meet).days
    n = (d // 100 + 1) * 100
    cands = [(meet + timedelta(days=n), f"{n} дней вместе")]
    ann = _safe_date(today.year, meet.month, meet.day)
    if ann <= today:
        ann = _safe_date(today.year + 1, meet.month, meet.day)
    years = ann.year - meet.year
    if years >= 1:
        cands.append((ann, f"{years} {plural(years, 'год', 'года', 'лет')} вместе"))
    target, label = min(cands, key=lambda x: x[0])
    return label, (target - today).days