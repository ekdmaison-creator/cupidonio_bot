import sqlite3
from contextlib import closing
from datetime import timedelta

from config import DB_PATH, TRIAL_DAYS, ACHIEVEMENTS, now, today_str

DATE_FMT = "%Y-%m-%d"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_one(sql, params=()):
    with closing(_conn()) as conn:
        return conn.execute(sql, params).fetchone()


def fetch_all(sql, params=()):
    with closing(_conn()) as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql, params=()):
    """Выполняет запрос и возвращает число изменённых строк."""
    with closing(_conn()) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount


def insert(sql, params=()):
    """Выполняет INSERT и возвращает id новой строки."""
    with closing(_conn()) as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def _add_column(conn, table, definition):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
    except sqlite3.OperationalError:
        pass  # колонка уже есть


def init_db():
    with closing(_conn()) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, name TEXT, partner_name TEXT,
            meeting_date TEXT, meeting_place TEXT, hobbies TEXT,
            favorite_movie TEXT, love_language TEXT,
            subscription_end TEXT, registered_at TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY, frequency TEXT DEFAULT 'daily')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS completed_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            task_date TEXT, task_text TEXT, completed INTEGER DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS task_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            task_text TEXT, feedback TEXT, created_at TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS moments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            text TEXT, created_at TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS achievements (
            user_id INTEGER, code TEXT, unlocked_at TEXT,
            PRIMARY KEY (user_id, code))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS referrals (
            referred_id INTEGER PRIMARY KEY, referrer_id INTEGER, created_at TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan TEXT,
            amount INTEGER, days INTEGER, status TEXT DEFAULT 'pending',
            created_at TEXT)''')

        # Миграции: безопасно добавляем новые колонки в старую базу
        _add_column(conn, "users", "user_gender TEXT DEFAULT 'не указан'")
        _add_column(conn, "users", "partner_gender TEXT DEFAULT 'не указан'")
        _add_column(conn, "users", "relationship_state TEXT DEFAULT 'отлично'")
        _add_column(conn, "users", "last_checkin TEXT DEFAULT ''")
        _add_column(conn, "users", "current_mood TEXT DEFAULT ''")
        _add_column(conn, "users", "partner_birthday TEXT DEFAULT ''")
        _add_column(conn, "users", "referred_by INTEGER DEFAULT 0")
        _add_column(conn, "users", "paywall_sent INTEGER DEFAULT 0")
        _add_column(conn, "users", "username TEXT DEFAULT ''")
        _add_column(conn, "completed_tasks", "category TEXT DEFAULT ''")
        _add_column(conn, "completed_tasks", "reaction TEXT DEFAULT ''")
        conn.commit()
    print("База данных готова!")


init_db()


# ============ ПОЛЬЗОВАТЕЛИ ============

def add_user(user_id, data, username="", referred_by=0):
    sub_end = (now() + timedelta(days=TRIAL_DAYS)).strftime(DATE_FMT)
    with closing(_conn()) as conn:
        conn.execute('''INSERT OR REPLACE INTO users
            (user_id, name, partner_name, meeting_date, meeting_place, hobbies,
             favorite_movie, love_language, subscription_end, registered_at,
             user_gender, partner_gender, relationship_state, username, referred_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (user_id, data["name"], data["partner_name"], data["meeting_date"],
             data["meeting_place"], data["hobbies"], data["favorite_movie"],
             data["love_language"], sub_end, today_str(),
             data.get("user_gender", "не указан"),
             data.get("partner_gender", "не указан"),
             data.get("relationship_state", "отлично"), username, referred_by))
        conn.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
        conn.commit()


def get_user(user_id):
    row = fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return dict(row) if row else None


def get_sub_end(user_id):
    row = fetch_one("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
    return row["subscription_end"] if row else ""


def is_active(user_id) -> bool:
    end = get_sub_end(user_id)
    return bool(end) and end >= today_str()


def days_left(user_id) -> int:
    end = get_sub_end(user_id)
    if not end:
        return 0
    delta = (now().date() - now().date()).days  # заглушка для типов
    from datetime import datetime
    delta = (datetime.strptime(end, DATE_FMT).date() - now().date()).days
    return max(delta, 0)


def extend_subscription(user_id, days=30):
    end = get_sub_end(user_id)
    if not end:
        return
    base = max(end, today_str())
    from datetime import datetime
    new_end = datetime.strptime(base, DATE_FMT) + timedelta(days=days)
    execute("UPDATE users SET subscription_end = ?, paywall_sent = 0 WHERE user_id = ?",
            (new_end.strftime(DATE_FMT), user_id))


def get_active_user_ids():
    rows = fetch_all("SELECT user_id FROM users WHERE subscription_end >= ?", (today_str(),))
    return [r["user_id"] for r in rows]


def get_expiring_tomorrow_ids():
    tomorrow = (now() + timedelta(days=1)).strftime(DATE_FMT)
    rows = fetch_all("SELECT user_id FROM users WHERE subscription_end = ? AND paywall_sent = 0",
                     (tomorrow,))
    return [r["user_id"] for r in rows]


def get_expired_unnotified_ids():
    rows = fetch_all("SELECT user_id FROM users WHERE subscription_end < ? AND paywall_sent < 2",
                     (today_str(),))
    return [r["user_id"] for r in rows]


def set_paywall_sent(user_id, value):
    execute("UPDATE users SET paywall_sent = ? WHERE user_id = ?", (value, user_id))


# ============ НАСТРОЙКИ ============

def get_setting(user_id):
    row = fetch_one("SELECT frequency FROM user_settings WHERE user_id = ?", (user_id,))
    return row["frequency"] if row else "daily"


def update_setting(user_id, frequency):
    execute("INSERT OR REPLACE INTO user_settings (user_id, frequency) VALUES (?, ?)",
            (user_id, frequency))


def set_birthday(user_id, value):
    execute("UPDATE users SET partner_birthday = ? WHERE user_id = ?", (value, user_id))


# ============ ЗАДАНИЯ ============
# completed: 0 — ждёт выполнения, 1 — выполнено, -1 — заменено другим заданием

def save_task(user_id, task_text, category=""):
    today = today_str()
    execute("UPDATE completed_tasks SET completed = -1 WHERE user_id = ? AND task_date = ? AND completed = 0",
            (user_id, today))
    insert("INSERT INTO completed_tasks (user_id, task_date, task_text, completed, category) VALUES (?,?,?,0,?)",
           (user_id, today, task_text, category))


def get_today_task(user_id):
    """Возвращает (task_text, completed) или None."""
    row = fetch_one('''SELECT task_text, completed FROM completed_tasks
        WHERE user_id = ? AND task_date = ? AND completed != -1
        ORDER BY id DESC LIMIT 1''', (user_id, today_str()))
    return (row["task_text"], row["completed"]) if row else None


def count_today_tasks(user_id):
    row = fetch_one("SELECT COUNT(*) c FROM completed_tasks WHERE user_id = ? AND task_date = ?",
                    (user_id, today_str()))
    return row["c"]


def mark_done(user_id):
    return execute('''UPDATE completed_tasks SET completed = 1 WHERE id = (
        SELECT id FROM completed_tasks WHERE user_id = ? AND task_date = ? AND completed = 0
        ORDER BY id DESC LIMIT 1)''', (user_id, today_str())) > 0


def set_reaction(user_id, reaction):
    execute('''UPDATE completed_tasks SET reaction = ? WHERE id = (
        SELECT id FROM completed_tasks WHERE user_id = ? AND task_date = ? AND completed = 1
        ORDER BY id DESC LIMIT 1)''', (reaction, user_id, today_str()))


def _title(text):
    first = (text or "").strip().split("\n")[0]
    return first.replace("🎯", "").strip()


def get_recent_task_titles(user_id, limit=10):
    rows = fetch_all("SELECT task_text FROM completed_tasks WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                     (user_id, limit))
    return [_title(r["task_text"]) for r in rows]


def get_liked_titles(user_id, limit=3):
    rows = fetch_all('''SELECT task_text FROM completed_tasks
        WHERE user_id = ? AND reaction = 'love' ORDER BY id DESC LIMIT ?''', (user_id, limit))
    return [_title(r["task_text"]) for r in rows]


def get_last_category(user_id):
    row = fetch_one("SELECT category FROM completed_tasks WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                    (user_id,))
    return row["category"] if row and row["category"] else ""


def get_users_with_pending_task():
    rows = fetch_all('''SELECT DISTINCT c.user_id FROM completed_tasks c
        JOIN users u ON u.user_id = c.user_id
        WHERE c.task_date = ? AND c.completed = 0 AND u.subscription_end >= ?''',
        (today_str(), today_str()))
    return [r["user_id"] for r in rows]


# ============ СТАТИСТИКА И ДОСТИЖЕНИЯ ============

def get_stats(user_id):
    """Возвращает (за 7 дней, за 30 дней, серия, всего дней с выполненными заданиями)."""
    today = now().date()
    rows = fetch_all("SELECT DISTINCT task_date FROM completed_tasks WHERE user_id = ? AND completed = 1",
                     (user_id,))
    done = {r["task_date"] for r in rows}
    week_start = (today - timedelta(days=6)).strftime(DATE_FMT)
    month_start = (today - timedelta(days=29)).strftime(DATE_FMT)
    week = sum(1 for d in done if d >= week_start)
    month = sum(1 for d in done if d >= month_start)

    freq = get_setting(user_id)
    streak = 0
    day = today
    if day.strftime(DATE_FMT) not in done:
        day -= timedelta(days=1)  # сегодняшнее ещё можно выполнить — серия не сгорает
    while True:
        if day.strftime(DATE_FMT) in done:
            streak += 1
        elif freq == "3times_week" and day.weekday() not in (0, 2, 4):
            pass  # в «нерабочие» дни серия не прерывается
        else:
            break
        day -= timedelta(days=1)
    return week, month, streak, len(done)


def unlock_achievement(user_id, code) -> bool:
    return execute("INSERT OR IGNORE INTO achievements (user_id, code, unlocked_at) VALUES (?,?,?)",
                   (user_id, code, today_str())) > 0


def get_achievements(user_id):
    rows = fetch_all("SELECT code FROM achievements WHERE user_id = ?", (user_id,))
    return {r["code"] for r in rows}


def check_achievements(user_id):
    """Проверяет условия и возвращает коды НОВЫХ достижений."""
    week, month, streak, total = get_stats(user_id)
    conditions = {
        "first_task": total >= 1,
        "streak_3": streak >= 3,
        "streak_7": streak >= 7,
        "streak_30": streak >= 30,
        "total_10": total >= 10,
        "total_50": total >= 50,
        "first_moment": count_moments(user_id) >= 1,
        "ambassador": count_referrals(user_id) >= 1,
    }
    return [c for c, ok in conditions.items() if ok and c in ACHIEVEMENTS and unlock_achievement(user_id, c)]


# ============ ФИДБЕК, СОСТОЯНИЕ ============

def save_feedback(user_id, task_text, feedback):
    insert("INSERT INTO task_feedback (user_id, task_text, feedback, created_at) VALUES (?,?,?,?)",
           (user_id, task_text, feedback, now().strftime("%Y-%m-%d %H:%M:%S")))


def get_recent_feedback(user_id, limit=3):
    rows = fetch_all("SELECT feedback FROM task_feedback WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                     (user_id, limit))
    return [r["feedback"] for r in rows]


def update_relationship_state(user_id, state):
    execute("UPDATE users SET relationship_state = ? WHERE user_id = ?", (state, user_id))


def get_relationship_state(user_id):
    row = fetch_one("SELECT relationship_state FROM users WHERE user_id = ?", (user_id,))
    return row["relationship_state"] if row and row["relationship_state"] else "отлично"


def update_checkin(user_id, mood):
    execute("UPDATE users SET last_checkin = ?, current_mood = ? WHERE user_id = ?",
            (today_str(), mood, user_id))


def days_since_checkin(user_id):
    row = fetch_one("SELECT last_checkin FROM users WHERE user_id = ?", (user_id,))
    if not row or not row["last_checkin"]:
        return 999
    from datetime import datetime
    try:
        return (now() - datetime.strptime(row["last_checkin"], DATE_FMT)).days
    except ValueError:
        return 999


# ============ ДНЕВНИК ============

def add_moment(user_id, text):
    insert("INSERT INTO moments (user_id, text, created_at) VALUES (?,?,?)",
           (user_id, text, now().strftime("%d.%m.%Y")))


def get_moments(user_id, limit=5):
    return fetch_all("SELECT text, created_at FROM moments WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                     (user_id, limit))


def count_moments(user_id):
    return fetch_one("SELECT COUNT(*) c FROM moments WHERE user_id = ?", (user_id,))["c"]


# ============ РЕФЕРАЛЫ ============

def add_referral(referrer_id, referred_id) -> bool:
    return execute("INSERT OR IGNORE INTO referrals (referred_id, referrer_id, created_at) VALUES (?,?,?)",
                   (referred_id, referrer_id, today_str())) > 0


def count_referrals(user_id):
    return fetch_one("SELECT COUNT(*) c FROM referrals WHERE referrer_id = ?", (user_id,))["c"]


# ============ ОПЛАТЫ ============

def create_payment(user_id, plan, amount, days):
    return insert("INSERT INTO payments (user_id, plan, amount, days, created_at) VALUES (?,?,?,?,?)",
                  (user_id, plan, amount, days, now().strftime("%Y-%m-%d %H:%M")))


def get_payment(payment_id):
    row = fetch_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
    return dict(row) if row else None


def set_payment_status(payment_id, status) -> bool:
    """Меняет статус только у платежа в ожидании (защита от двойного нажатия)."""
    return execute("UPDATE payments SET status = ? WHERE id = ? AND status = 'pending'",
                   (status, payment_id)) > 0


def admin_stats():
    today = today_str()
    paid = fetch_one('''SELECT COUNT(*) c, COALESCE(SUM(amount),0) s,
        COUNT(DISTINCT user_id) u FROM payments WHERE status = 'approved' ''')
    return {
        "total": fetch_one("SELECT COUNT(*) c FROM users")["c"],
        "new_today": fetch_one("SELECT COUNT(*) c FROM users WHERE registered_at = ?", (today,))["c"],
        "active": fetch_one("SELECT COUNT(*) c FROM users WHERE subscription_end >= ?", (today,))["c"],
        "payments": paid["c"], "revenue": paid["s"], "payers": paid["u"],
    }