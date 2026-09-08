import sqlite3
from datetime import datetime, timedelta

def init_db():
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    
    # Таблица пользователей
    cur.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        name TEXT,
        partner_name TEXT,
        meeting_date TEXT,
        meeting_place TEXT,
        hobbies TEXT,
        favorite_movie TEXT,
        love_language TEXT,
        subscription_end TEXT,
        registered_at TEXT
    )
    ''')
    
    # Таблица настроек частоты
    cur.execute('''
    CREATE TABLE IF NOT EXISTS user_settings (
        user_id INTEGER PRIMARY KEY,
        frequency TEXT DEFAULT 'daily'
    )
    ''')
    
    # Таблица выполненных заданий
    cur.execute('''
    CREATE TABLE IF NOT EXISTS completed_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_date TEXT,
        task_text TEXT,
        completed INTEGER DEFAULT 0
    )
    ''')
    
    conn.commit()
    conn.close()
    print("База данных создана!")

# Запускаем создание таблиц при импорте
init_db()

# --- Функции для работы с пользователями ---

def add_user(user_id, name, partner_name, meeting_date, meeting_place, hobbies, favorite_movie, love_language):
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d')
    sub_end = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
    
    cur.execute('''
    INSERT INTO users (user_id, name, partner_name, meeting_date, meeting_place, hobbies, favorite_movie, love_language, subscription_end, registered_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, name, partner_name, meeting_date, meeting_place, hobbies, favorite_movie, love_language, sub_end, now))
    
    cur.execute('INSERT INTO user_settings (user_id) VALUES (?)', (user_id,))
    
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cur.fetchone()
    conn.close()
    return user

def check_subscription(user_id):
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    cur.execute('SELECT subscription_end FROM users WHERE user_id = ?', (user_id,))
    result = cur.fetchone()
    conn.close()
    if result:
        sub_end = datetime.strptime(result[0], '%Y-%m-%d')
        return sub_end >= datetime.now()
    return False

def get_setting(user_id):
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    cur.execute('SELECT frequency FROM user_settings WHERE user_id = ?', (user_id,))
    result = cur.fetchone()
    conn.close()
    return result[0] if result else 'daily'

def update_setting(user_id, frequency):
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    cur.execute('UPDATE user_settings SET frequency = ? WHERE user_id = ?', (frequency, user_id))
    conn.commit()
    conn.close()

def save_task(user_id, task_text):
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cur.execute('''
    INSERT INTO completed_tasks (user_id, task_date, task_text, completed)
    VALUES (?, ?, ?, 0)
    ''', (user_id, today, task_text))
    conn.commit()
    conn.close()

def mark_done(user_id):
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cur.execute('''
    UPDATE completed_tasks SET completed = 1 
    WHERE user_id = ? AND task_date = ?
    ''', (user_id, today))
    conn.commit()
    conn.close()
    return cur.rowcount > 0

def get_stats(user_id):
    conn = sqlite3.connect('cupidon.db')
    cur = conn.cursor()
    today = datetime.now()
    
    # За неделю
    week_ago = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    cur.execute('''
    SELECT COUNT(*) FROM completed_tasks 
    WHERE user_id = ? AND task_date >= ? AND completed = 1
    ''', (user_id, week_ago))
    week_count = cur.fetchone()[0]
    
    # За месяц
    month_ago = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    cur.execute('''
    SELECT COUNT(*) FROM completed_tasks 
    WHERE user_id = ? AND task_date >= ? AND completed = 1
    ''', (user_id, month_ago))
    month_count = cur.fetchone()[0]
    
    # Серия (streak)
    streak = 0
    check_date = today
    while True:
        date_str = check_date.strftime('%Y-%m-%d')
        cur.execute('''
        SELECT completed FROM completed_tasks 
        WHERE user_id = ? AND task_date = ?
        ''', (user_id, date_str))
        result = cur.fetchone()
        if result and result[0] == 1:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break
    
    conn.close()
    return week_count, month_count, streak