"""
Модуль для работы с базами данных (used_videos, queue, groups, stats_daily).
"""

import sqlite3
import random
from config.config import DB_USED, DB_QUEUE, DB_GROUPS, DAILY_LIMIT_DEFAULT

# ---------- Инициализация ----------
def init_dbs():
    with sqlite3.connect(DB_USED) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS used_videos (id TEXT PRIMARY KEY, date TEXT)")
    with sqlite3.connect(DB_QUEUE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id TEXT PRIMARY KEY,
                url TEXT,
                views INTEGER,
                upload_date TEXT,
                title TEXT,
                duration INTEGER,
                likes INTEGER,
                description TEXT,
                channel TEXT,
                processed INTEGER DEFAULT 0,
                downloaded_at TIMESTAMP,
                group_id INTEGER,
                attempts INTEGER DEFAULT 0,
                error TEXT
            )
        """)
    with sqlite3.connect(DB_GROUPS) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                group_id INTEGER UNIQUE,
                access_token TEXT,
                active INTEGER DEFAULT 1,
                daily_limit INTEGER DEFAULT 6,
                last_posted_ts INTEGER DEFAULT 0
            )
        """)
        try:
            conn.execute("ALTER TABLE groups ADD COLUMN last_posted_ts INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    # Таблица для ежедневной статистики
    with sqlite3.connect(DB_USED) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stats_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                group_id INTEGER NOT NULL,
                total_clips INTEGER DEFAULT 0,
                total_views INTEGER DEFAULT 0,
                deleted_clips INTEGER DEFAULT 0,
                UNIQUE(date, group_id)
            )
        """)

# ---------- used_videos ----------
def add_to_used_videos(video_id):
    with sqlite3.connect(DB_USED) as conn:
        conn.execute("INSERT OR REPLACE INTO used_videos (id, date) VALUES (?, date('now'))", (video_id,))

def is_video_used(video_id):
    with sqlite3.connect(DB_USED) as conn:
        cursor = conn.execute("SELECT 1 FROM used_videos WHERE id = ?", (video_id,))
        return cursor.fetchone() is not None

def clean_old_used_videos(days=20):
    with sqlite3.connect(DB_USED) as conn:
        conn.execute(f"DELETE FROM used_videos WHERE date < date('now', '-{days} days')")

def clean_old_queue(days=60):
    """Удаляет обработанные записи из queue старше N дней."""
    with sqlite3.connect(DB_QUEUE) as conn:
        conn.execute(
            f"DELETE FROM queue WHERE processed = 1 "
            f"AND date(downloaded_at) < date('now', '-{days} days')"
        )

# ---------- queue ----------
def add_to_queue(video_data):
    with sqlite3.connect(DB_QUEUE) as conn:
        cursor = conn.execute("SELECT 1 FROM queue WHERE id = ?", (video_data['id'],))
        if cursor.fetchone():
            return
        conn.execute("""
            INSERT INTO queue (id, url, views, upload_date, title, duration, likes, description, channel, processed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            video_data['id'],
            video_data['url'],
            video_data['views'],
            video_data['upload_date'],
            video_data['title'],
            video_data['duration'],
            video_data['likes'],
            video_data['description'],
            video_data['channel']
        ))

def get_queue_count(processed=None):
    with sqlite3.connect(DB_QUEUE) as conn:
        if processed is None:
            cursor = conn.execute("SELECT COUNT(*) FROM queue")
        else:
            cursor = conn.execute("SELECT COUNT(*) FROM queue WHERE processed = ?", (processed,))
        return cursor.fetchone()[0]

def get_next_video():
    with sqlite3.connect(DB_QUEUE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM queue WHERE processed = 0 ORDER BY RANDOM() LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None

def mark_as_processed(video_id, group_id=None):
    with sqlite3.connect(DB_QUEUE) as conn:
        conn.execute("""
            UPDATE queue SET processed = 1, downloaded_at = CURRENT_TIMESTAMP, group_id = ?
            WHERE id = ?
        """, (group_id, video_id))

def mark_as_error(video_id, error_msg):
    with sqlite3.connect(DB_QUEUE) as conn:
        conn.execute("""
            UPDATE queue SET attempts = attempts + 1, error = ?
            WHERE id = ?
        """, (error_msg, video_id))

# ---------- groups ----------
def add_group(name, group_id, access_token, daily_limit=6):
    with sqlite3.connect(DB_GROUPS) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO groups (name, group_id, access_token, daily_limit, last_posted_ts)
            VALUES (?, ?, ?, ?, 0)
        """, (name, group_id, access_token, daily_limit))

def get_active_groups():
    with sqlite3.connect(DB_GROUPS) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM groups WHERE active = 1")
        return [dict(row) for row in cursor.fetchall()]

def get_group_token(group_id):
    """
    Возвращает единый пользовательский токен из .env.
    Читает .env каждый раз — чтобы видеть свежий токен после обновления
    (без перезапуска процесса).
    """
    from pathlib import Path
    try:
        from config.config import ROOT_DIR
        env_file = Path(ROOT_DIR) / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("VK_ACCESS_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    # Fallback — значение, загруженное при старте
    from config.config import VK_ACCESS_TOKEN
    return VK_ACCESS_TOKEN



def update_group_last_posted(group_id, timestamp):
    with sqlite3.connect(DB_GROUPS) as conn:
        conn.execute("UPDATE groups SET last_posted_ts = ? WHERE group_id = ?", (timestamp, group_id))

# ---------- Лимиты ----------
def get_group_posts_today(group_id):
    with sqlite3.connect(DB_QUEUE) as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM queue WHERE group_id = ? AND date(downloaded_at) = date('now')",
            (group_id,)
        )
        return cursor.fetchone()[0]

def get_group_daily_limit(group_id):
    with sqlite3.connect(DB_GROUPS) as conn:
        cursor = conn.execute("SELECT daily_limit FROM groups WHERE group_id = ?", (group_id,))
        row = cursor.fetchone()
        if row and row[0] is not None:
            return row[0]
        return DAILY_LIMIT_DEFAULT

def get_available_group():
    import time
    from config.config import GROUP_COOLDOWN

    groups = get_active_groups()
    if not groups:
        return None

    random.shuffle(groups)
    now = int(time.time())

    for g in groups:
        group_id = g["group_id"]
        daily_limit = get_group_daily_limit(group_id)
        today_posts = get_group_posts_today(group_id)

        if today_posts >= daily_limit:
            continue

        last_posted = g.get("last_posted_ts", 0)
        if now - last_posted < GROUP_COOLDOWN:
            continue

        return g

    return None

def get_random_group():
    groups = get_active_groups()
    return random.choice(groups) if groups else None

# ---------- stats_daily ----------
def save_daily_stats(date_str, group_id, total_clips, total_views, deleted_clips=0):
    with sqlite3.connect(DB_USED) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO stats_daily (date, group_id, total_clips, total_views, deleted_clips)
            VALUES (?, ?, ?, ?, ?)
        """, (date_str, group_id, total_clips, total_views, deleted_clips))

def get_daily_stats(date_str, group_id):
    with sqlite3.connect(DB_USED) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM stats_daily WHERE date = ? AND group_id = ?",
            (date_str, group_id)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

def get_stats_for_period(group_id, start_date, end_date):
    with sqlite3.connect(DB_USED) as conn:
        cursor = conn.execute("""
            SELECT SUM(total_clips) as clips, SUM(total_views) as views
            FROM stats_daily
            WHERE group_id = ? AND date BETWEEN ? AND ?
        """, (group_id, start_date, end_date))
        row = cursor.fetchone()
        return {'clips': row[0] or 0, 'views': row[1] or 0}


# ---------- stats_daily: атомарный инкремент ----------
def increment_deleted_clips(date_str, group_id, delta):
    """
    Атомарно прибавляет delta к deleted_clips в stats_daily для (date, group_id).
    Если записи нет — создаёт её с нулевыми clips/views и указанным deleted.
    """
    with sqlite3.connect(DB_USED) as conn:
        cur = conn.execute(
            "UPDATE stats_daily SET deleted_clips = deleted_clips + ? "
            "WHERE date = ? AND group_id = ?",
            (delta, date_str, group_id),
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO stats_daily "
                "(date, group_id, total_clips, total_views, deleted_clips) "
                "VALUES (?, ?, 0, 0, ?)",
                (date_str, group_id, delta),
            )


# ---------- stats_daily: сохранить дневные clips/views без затирания deleted ----------
def save_daily_stats_keep_deleted(date_str, group_id, total_clips, total_views):
    """
    Обновляет total_clips / total_views, НЕ затирая deleted_clips.
    Если записи нет — создаёт её с deleted_clips = 0.
    """
    with sqlite3.connect(DB_USED) as conn:
        cur = conn.execute(
            "UPDATE stats_daily SET total_clips = ?, total_views = ? "
            "WHERE date = ? AND group_id = ?",
            (total_clips, total_views, date_str, group_id),
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO stats_daily "
                "(date, group_id, total_clips, total_views, deleted_clips) "
                "VALUES (?, ?, ?, ?, 0)",
                (date_str, group_id, total_clips, total_views),
            )
