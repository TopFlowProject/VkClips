#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Отправка сообщений через VK-сообщество (StatsBots).

Приоритет токенов:
  1. VK_GROUP_TOKEN  (сообщество) — шлёт от имени бота, from_group=1
  2. VK_ACCESS_TOKEN (наш user)  — fallback
  3. VK_USER_TOKEN   (внешний)   — fallback

Получатель — VK_ALERT_PEER_ID (или VK_ADMIN_ID).
"""

import os
import time
import random
import logging
import threading
from typing import Optional, List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("notifier")

VK_API = "https://api.vk.com/method/messages.send"
VK_VERSION = "5.199"
MAX_CHUNK = 4000

_last_send_ts = 0.0
_min_interval = 1.0
_send_lock = threading.Lock()

_working_token_name: Optional[str] = None


def _get_peer_id() -> Optional[int]:
    raw = os.getenv("VK_ALERT_PEER_ID") or os.getenv("VK_ADMIN_ID")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _candidates() -> List[Tuple[str, str, bool, Optional[int]]]:
    """Список (name, token, from_group, group_id) в порядке приоритета."""
    gtok = (os.getenv("VK_GROUP_TOKEN") or "").strip()
    gid_raw = (os.getenv("VK_GROUP_ID") or "").strip()
    gid = int(gid_raw) if gid_raw.isdigit() else None
    atok = (os.getenv("VK_ACCESS_TOKEN") or "").strip()
    utok = (os.getenv("VK_USER_TOKEN") or "").strip()

    pool = []
    if gtok:
        pool.append(("VK_GROUP_TOKEN", gtok, True, gid))
    if atok and atok != gtok:
        pool.append(("VK_ACCESS_TOKEN", atok, False, None))
    if utok and utok not in (gtok, atok):
        pool.append(("VK_USER_TOKEN", utok, False, None))

    if _working_token_name:
        pool.sort(key=lambda x: 0 if x[0] == _working_token_name else 1)
    return pool


def _chunk(text: str, size: int = MAX_CHUNK):
    lines = text.split("\n")
    buf, length = [], 0
    for line in lines:
        if length + len(line) + 1 > size and buf:
            yield "\n".join(buf)
            buf, length = [], 0
        buf.append(line)
        length += len(line) + 1
    if buf:
        yield "\n".join(buf)


def _send_with_token(text: str, token: str, peer_id: int,
                     from_group: bool, group_id: Optional[int]):
    data = {
        "access_token": token,
        "v": VK_VERSION,
        "peer_id": peer_id,
        "message": text,
        "random_id": random.randint(1, 2**31 - 1),
    }
    if from_group:
        data["from_group"] = 1
        if group_id:
            data["group_id"] = group_id
    try:
        resp = requests.post(VK_API, data=data, timeout=10)
        rj = resp.json()
        if "error" in rj:
            err = rj["error"]
            return False, f"[{err.get('error_code')}] {err.get('error_msg')}"
        return True, None
    except Exception as e:
        return False, f"exception: {e}"


def _send_one(text: str, peer_id: int) -> bool:
    global _last_send_ts, _working_token_name

    pool = _candidates()
    if not pool:
        logger.warning("Нет ни одного токена в .env — сообщение пропущено")
        return False

    with _send_lock:
        delta = time.time() - _last_send_ts
        if delta < _min_interval:
            time.sleep(_min_interval - delta)

        errors = []
        for name, token, from_group, gid in pool:
            ok, err = _send_with_token(text, token, peer_id, from_group, gid)
            if ok:
                _last_send_ts = time.time()
                if _working_token_name != name:
                    logger.info("VK: используется %s", name)
                    _working_token_name = name
                return True
            errors.append(f"{name}: {err}")
            logger.warning("VK send via %s failed: %s", name, err)

        logger.error("Все токены не сработали: %s", " | ".join(errors))
        return False


def send_alert(text: str, prefix: Optional[str] = None) -> bool:
    peer_id = _get_peer_id()
    if not peer_id:
        logger.warning("VK_ALERT_PEER_ID / VK_ADMIN_ID не заданы")
        return False
    body = f"{prefix}\n{text}" if prefix else text
    ok = True
    for chunk in _chunk(body):
        if not _send_one(chunk, peer_id):
            ok = False
    return ok


def alert_info(text: str) -> bool:  return send_alert(text, prefix="ℹ️")
def alert_warn(text: str) -> bool:  return send_alert(text, prefix="⚠️")
def alert_error(text: str) -> bool: return send_alert(text, prefix="🚨")
def alert_ok(text: str) -> bool:    return send_alert(text, prefix="✅")
