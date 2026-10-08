#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import time
import logging
from logging.handlers import TimedRotatingFileHandler
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.config import LOGS_DIR, VK_ACCESS_TOKEN, VK_API_VERSION, DB_QUEUE
from core.db import init_dbs, get_active_groups, get_group_token, get_daily_stats, save_daily_stats, save_daily_stats_keep_deleted

LOG_FILE = LOGS_DIR / "stats_collector.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        TimedRotatingFileHandler(LOG_FILE, when="midnight", backupCount=14, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("stats_collector")

def safe_int(value):
    """Безопасно преобразует значение в int, обрабатывая словари и строки."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        # Пытаемся взять суммарное значение из 'total', 'value' или первого числового поля
        if 'total' in value:
            return safe_int(value['total'])
        if 'value' in value:
            return safe_int(value['value'])
        # Если словарь содержит числовые значения, возьмём сумму
        if all(isinstance(v, (int, float)) for v in value.values()):
            return int(sum(value.values()))
        return 0
    if isinstance(value, str):
        try:
            return int(value)
        except:
            return 0
    return 0

# ---------- VK API helpers ----------
def get_group_members_count(group_id: int, token: str) -> int:
    url = "https://api.vk.com/method/groups.getById"
    params = {
        "group_id": abs(group_id),
        "fields": "members_count",
        "access_token": token,
        "v": VK_API_VERSION,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if "error" not in data:
            return data.get("response", [{}])[0].get("members_count", 0)
    except Exception as e:
        logger.error(f"Ошибка получения подписчиков для {group_id}: {e}")
    return 0

def get_group_stats(group_id: int, token: str, date_str: str) -> Dict:
    url = "https://api.vk.com/method/stats.get"
    params = {
        "group_id": abs(group_id),
        "date": date_str,
        "access_token": token,
        "v": VK_API_VERSION,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if "error" in data:
            logger.error(f"Ошибка stats.get для {group_id}: {data['error']['error_msg']}")
            return {}
        items = data.get("response", [])
        if items and isinstance(items, list) and len(items) > 0:
            item = items[0]
            return {
                "reach": safe_int(item.get("reach")),
                "reach_subscribers": safe_int(item.get("reach_subscribers")),
                "views": safe_int(item.get("views")),
                "subscribed": safe_int(item.get("subscribed")),
                "unsubscribed": safe_int(item.get("unsubscribed")),
            }
        return {}
    except Exception as e:
        logger.error(f"Ошибка запроса stats.get: {e}")
        return {}

def get_video_views_batch(owner_id: int, video_ids: List[int], token: str) -> Dict[int, int]:
    if not video_ids:
        return {}
    ids_str = ','.join([f"{owner_id}_{vid}" for vid in video_ids])
    url = "https://api.vk.com/method/shortVideo.getById"
    params = {
        "videos": ids_str,
        "access_token": token,
        "v": VK_API_VERSION,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if "error" not in data:
            items = data.get("response", {}).get("items", [])
            return {item["id"]: item.get("views", 0) for item in items}
    except Exception as e:
        logger.error(f"Ошибка получения просмотров: {e}")
    return {}

def get_top_clip_yesterday(group_id: int, token: str, yesterday: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_QUEUE)
    cursor = conn.execute("""
        SELECT id FROM queue
        WHERE group_id = ? AND processed = 1 AND date(downloaded_at) = ?
    """, (group_id, yesterday))
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return None
    video_ids = [row[0] for row in rows]
    views_dict = get_video_views_batch(group_id, video_ids, token)
    if not views_dict:
        return None
    best_id = max(views_dict, key=lambda x: views_dict[x])
    return {"video_id": best_id, "views": views_dict[best_id]}

# ---------- Сбор статистики ----------
def collect_all_stats():
    groups = get_active_groups()
    if not groups:
        logger.warning("Нет активных групп")
        return None

    default_token = VK_ACCESS_TOKEN
    if not default_token:
        logger.error("Нет VK_ACCESS_TOKEN в конфиге")
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    result = {}

    for group in groups:
        group_id = group["group_id"]
        token = get_group_token(group_id) or default_token
        if not token:
            logger.warning(f"Нет токена для группы {group_id}, пропускаем")
            continue

        logger.info(f"Сбор данных для группы {group_id}...")

        members = get_group_members_count(group_id, token)
        stats = get_group_stats(group_id, token, yesterday)

        reach_total = stats.get("reach", 0)
        reach_subscribers = stats.get("reach_subscribers", 0)
        views_total = stats.get("views", 0)
        net_gain = stats.get("subscribed", 0) - stats.get("unsubscribed", 0)

        # Данные из БД
        conn = sqlite3.connect(DB_QUEUE)

        def get_clips_count(start_date, end_date):
            cur = conn.execute(
                "SELECT COUNT(*) FROM queue WHERE group_id = ? AND processed = 1 AND date(downloaded_at) BETWEEN ? AND ?",
                (group_id, start_date, end_date)
            )
            return cur.fetchone()[0]

        clips_yesterday = get_clips_count(yesterday, yesterday)
        clips_week = get_clips_count(week_ago, yesterday)
        clips_month = get_clips_count(month_ago, yesterday)

        def get_views_for_period(start_date, end_date):
            cur = conn.execute(
                "SELECT id FROM queue WHERE group_id = ? AND processed = 1 AND date(downloaded_at) BETWEEN ? AND ?",
                (group_id, start_date, end_date)
            )
            ids = [row[0] for row in cur.fetchall()]
            if not ids:
                return 0
            total = 0
            for i in range(0, len(ids), 100):
                batch = ids[i:i+100]
                views_dict = get_video_views_batch(group_id, batch, token)
                total += sum(views_dict.values())
                time.sleep(0.3)
            return total

        views_yesterday = get_views_for_period(yesterday, yesterday)
        views_week = get_views_for_period(week_ago, yesterday)
        views_month = get_views_for_period(month_ago, yesterday)
        conn.close()

        daily_record = get_daily_stats(yesterday, group_id)
        deleted = daily_record.get("deleted_clips", 0) if daily_record else 0

        top = get_top_clip_yesterday(group_id, token, yesterday)
        avg_daily = round(views_month / 30) if views_month else 0

        # Сохраняем сегодняшний снимок (месячная динамика, без "за всё время")
        save_daily_stats_keep_deleted(yesterday, group_id, clips_yesterday, views_yesterday)

        result[str(group_id)] = {
            "name": group.get("name", f"Группа {group_id}"),
            "members": members,
            "net_gain": net_gain,
            "reach_total": reach_total,
            "reach_subscribers": reach_subscribers,
            "views_total": views_total,
            "clips_yesterday": clips_yesterday,
            "views_yesterday": views_yesterday,
            "clips_week": clips_week,
            "views_week": views_week,
            "clips_month": clips_month,
            "views_month": views_month,
            "avg_daily": avg_daily,
            "deleted": deleted,
            "top_clip": top,
        }
        logger.info(f"Группа {group_id}: собрано {clips_yesterday} клипов за вчера, {views_yesterday} просмотров")
        time.sleep(1)

    return result

# ---------- Формирование отчёта ----------
def format_report(data: Dict) -> str:
    lines = []
    lines.append("📊 ЕЖЕДНЕВНЫЙ ОТЧЁТ ПО ГРУППАМ")
    lines.append(f"🗓 {datetime.now().strftime('%d.%m.%Y')} (за вчера: {(datetime.now() - timedelta(days=1)).strftime('%d.%m.%Y')})")
    lines.append("=" * 40)

    for group_id, stats in data.items():
        lines.append(f"\n🔹 Группа «{stats['name']}» (ID: {group_id})")
        lines.append(f"   Ссылка: vk.com/club{abs(int(group_id))}")
        lines.append("   ──────────────────────────────────────────────")
        lines.append("")
        lines.append("   📈 ДИНАМИКА КЛИПОВ")
        lines.append(f"   За вчера:       +{stats['clips_yesterday']} клипа, +{stats['views_yesterday']:,} просмотров")
        lines.append(f"   За неделю:     +{stats['clips_week']} клипов, +{stats['views_week']:,} просмотров")
        lines.append(f"   За месяц:      +{stats['clips_month']} клипов, +{stats['views_month']:,} просмотров")
        lines.append(f"   Среднее за день (30 дн): {stats['avg_daily']:,} просмотров")
        lines.append(f"   Клипов удалено по медиане: {stats['deleted']}")
        if stats['top_clip']:
            lines.append(f"   Топ-клип за вчера: {stats['top_clip']['views']:,} просмотров")
            lines.append(f"      👉 vk.com/video-{abs(int(group_id))}_{stats['top_clip']['video_id']}")
        else:
            lines.append("   Топ-клип за вчера: не найден")
        lines.append("")
        lines.append("   📊 СТАТИСТИКА ГРУППЫ (из дашборда)")
        gain_str = f"+{stats['net_gain']}" if stats['net_gain'] >= 0 else str(stats['net_gain'])
        lines.append(f"   Подписчиков:           {stats['members']:,} ({gain_str} за вчера)")
        lines.append(f"   Охват (всего):         {stats['reach_total']:,}")
        lines.append(f"   Охват (подписчики):    {stats['reach_subscribers']:,}")
        lines.append(f"   Просмотры (все записи): {stats['views_total']:,}")

    lines.append("\n" + "=" * 40)
    lines.append("✅ Отчёт сформирован (отправка в ЛС отключена).")
    return "\n".join(lines)

# ---------- Основная функция ----------
def main():
    init_dbs()
    logger.info("Stats collector started")

    data = collect_all_stats()
    if not data:
        logger.warning("Нет данных для отчёта")
        return

    report = format_report(data)
    logger.info("\n" + report)
    logger.info("Stats collector finished")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
        sys.exit(0)