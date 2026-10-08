#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import logging
from logging.handlers import TimedRotatingFileHandler
import statistics
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.config import LOGS_DIR, VK_ACCESS_TOKEN, VK_API_VERSION
from core.db import init_dbs, get_active_groups, get_group_token, increment_deleted_clips

# Настройка логгера
LOG_FILE = LOGS_DIR / "vk_cleaner.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        TimedRotatingFileHandler(LOG_FILE, when="midnight", backupCount=14, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("vk_cleaner")

# Порог удаления (доля от медианы)
THRESHOLD = 0.35  # 35%
# Количество клипов, запрашиваемых за раз
CLIPS_PER_REQUEST = 50


def get_clips(owner_id: int, token: str, count: int = CLIPS_PER_REQUEST) -> List[Dict]:
    """Получает клипы группы через shortVideo.getOwnerVideos."""
    url = "https://api.vk.com/method/shortVideo.getOwnerVideos"
    params = {
        "owner_id": owner_id,
        "count": count,
        "access_token": token,
        "v": VK_API_VERSION,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"Ошибка запроса для группы {owner_id}: {e}")
        return []

    if "error" in data:
        logger.error(f"Ошибка VK API для группы {owner_id}: {data['error']['error_msg']}")
        return []

    return data.get("response", {}).get("items", [])


def delete_video(owner_id: int, video_id: int, token: str) -> bool:
    """Удаляет видео через video.delete."""
    url = "https://api.vk.com/method/video.delete"
    params = {
        "owner_id": owner_id,
        "video_id": video_id,
        "access_token": token,
        "v": VK_API_VERSION,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            logger.warning(f"Ошибка удаления {owner_id}_{video_id}: {data['error']['error_msg']}")
            return False
        return True
    except Exception as e:
        logger.error(f"Ошибка запроса при удалении {owner_id}_{video_id}: {e}")
        return False


def get_age_group(created_time: int) -> str:

    """young (<4 дней), medium (4–8), old (8–14), very_old (>14)."""

    days = (datetime.now() - datetime.fromtimestamp(created_time)).days

    if days < 4:

        return "young"

    elif days < 8:

        return "medium"

    elif days < 14:

        return "old"

    else:

        return "very_old"



def analyze_and_delete_group(group: dict, token: str, threshold: float = THRESHOLD):
    """
    Анализирует клипы одной группы и удаляет те, у которых просмотры ниже порога
    (порог = медиана просмотров * threshold) в каждой возрастной группе.
    """
    group_id = group["group_id"]
    group_name = group.get("name", f"group_{group_id}")
    logger.info(f"=== Обработка группы {group_name} (ID: {group_id}) ===")

    clips = get_clips(group_id, token, count=CLIPS_PER_REQUEST)
    if not clips:
        logger.info(f"Группа {group_id}: клипов не найдено")
        return

    logger.info(f"Получено {len(clips)} клипов")

    # Разбиваем по возрастным группам (игнорируем very_old)
    age_buckets = {"young": [], "medium": [], "old": [], "very_old": []}
    for clip in clips:
        created = clip.get("date")
        if not created:
            continue
        age = get_age_group(created)
        if age in age_buckets:
            age_buckets[age].append(clip)

    total_deleted = 0
    for age_group, bucket in age_buckets.items():
        if not bucket:
            logger.info(f"Возрастная группа '{age_group}': пусто")
            continue

        views = [c.get("views", 0) for c in bucket if "views" in c]
        if not views:
            logger.info(f"Возрастная группа '{age_group}': нет данных о просмотрах")
            continue

        median = statistics.median(views)
        lower_bound = median * threshold
        logger.info(
            f"Возрастная группа '{age_group}': клипов {len(bucket)}, "
            f"медиана {median:.0f}, порог удаления {lower_bound:.0f}"
        )

        to_delete = [c for c in bucket if c.get("views", 0) < lower_bound]
        if not to_delete:
            logger.info(f"Возрастная группа '{age_group}': нет клипов для удаления")
            continue

        logger.info(f"Найдено {len(to_delete)} клипов для удаления")

        for clip in to_delete:
            vid = clip.get("id")
            views_count = clip.get("views", 0)
            logger.info(f"  Удаляем {group_id}_{vid} (просмотров: {views_count})")
            if delete_video(group_id, vid, token):
                total_deleted += 1
            else:
                logger.warning(f"  Не удалось удалить {group_id}_{vid}")

    logger.info(f"Итого удалено клипов в группе {group_id}: {total_deleted}")

    # ---- СОХРАНЯЕМ КОЛИЧЕСТВО УДАЛЁННЫХ В STATS_DAILY ----
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    increment_deleted_clips(yesterday, group_id, total_deleted)
    # ---------------------------------------------------------


def main():
    init_dbs()
    logger.info("VK Cleaner started")

    groups = get_active_groups()
    if not groups:
        logger.warning("Нет активных групп для обработки")
        return

    default_token = VK_ACCESS_TOKEN
    if not default_token:
        logger.error("Нет токена доступа в конфиге (VK_ACCESS_TOKEN)")
        return

    for group in groups:
        group_id = group["group_id"]
        token = get_group_token(group_id) or default_token
        if not token:
            logger.warning(f"Для группы {group_id} нет токена, пропускаем")
            continue

        analyze_and_delete_group(group, token)
        time.sleep(2)

    logger.info("VK Cleaner finished")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
        sys.exit(0)