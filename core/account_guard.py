#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Учёт состояния VK-аккаунта:
  • блокировка на N дней после ошибки «user is blocked»
  • суточный лимит публикаций (per-account)
  • сколько секунд до следующего дня
"""

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Tuple

from config.config import DATA_DIR, DB_QUEUE

# -------- Настройки --------
BLOCKED_FILE = DATA_DIR / "vk_blocked_until.txt"
DAILY_LIMIT = 10       # максимум публикаций в сутки на один аккаунт
BLOCK_DAYS = 5         # сколько дней отдыхаем после «user is blocked»


# -------- Блокировка --------
def mark_blocked(days: int = BLOCK_DAYS) -> None:
    """Помечает аккаунт как заблокированный до now + days."""
    until = datetime.now() + timedelta(days=days)
    BLOCKED_FILE.parent.mkdir(parents=True, exist_ok=True)
    BLOCKED_FILE.write_text(until.isoformat(), encoding="utf-8")


def is_blocked() -> Tuple[bool, int]:
    """
    Возвращает (заблокирован: bool, секунд_до_разблокировки: int).
    Если файла нет или срок истёк — (False, 0) и файл удаляется.
    """
    if not BLOCKED_FILE.exists():
        return False, 0
    try:
        until = datetime.fromisoformat(
            BLOCKED_FILE.read_text(encoding="utf-8").strip()
        )
    except Exception:
        return False, 0
    delta = (until - datetime.now()).total_seconds()
    if delta <= 0:
        clear_block()
        return False, 0
    return True, int(delta)


def clear_block() -> None:
    try:
        BLOCKED_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def blocked_until_str() -> str:
    """Человекочитаемо: '25.09.2026 14:30' или '—'."""
    if not BLOCKED_FILE.exists():
        return "—"
    try:
        until = datetime.fromisoformat(
            BLOCKED_FILE.read_text(encoding="utf-8").strip()
        )
        return until.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "—"


# -------- Суточный лимит --------
def posts_today() -> int:
    """Сколько публикаций сегодня ушло в VK (по всем группам аккаунта)."""
    with sqlite3.connect(DB_QUEUE) as conn:
        cur = conn.execute(
            "SELECT COUNT(*) FROM queue "
            "WHERE processed = 1 "
            "  AND date(downloaded_at) = date('now')"
        )
        return cur.fetchone()[0]


def is_daily_limit_reached() -> bool:
    return posts_today() >= DAILY_LIMIT


def seconds_until_tomorrow() -> int:
    """Сколько секунд до 00:05 следующего дня."""
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=5, second=0, microsecond=0
    )
    return max(60, int((tomorrow - now).total_seconds()))
