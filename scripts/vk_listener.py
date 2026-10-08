#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Слушатель команд через сообщество-бота. LongPoll API, версия 5.199.

Команды:
    /help
    /stats day|week|month
    /status
"""

import os
import sys
import time
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.config import LOGS_DIR
from core.db import init_dbs, get_active_groups, get_group_token
from core.notifier import send_alert

VK_API = "https://api.vk.com/method/"
V = "5.199"

LOG_FILE = LOGS_DIR / "vk_listener.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        TimedRotatingFileHandler(LOG_FILE, when="midnight", backupCount=14, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("vk_listener")


# ---------------------------------------------------------------------------
# VK helpers
# ---------------------------------------------------------------------------
def vk_call(method, params, token, timeout=15):
    p = dict(params)
    p["access_token"] = token
    p["v"] = V
    try:
        return requests.get(VK_API + method, params=p, timeout=timeout).json()
    except Exception as e:
        logger.error("VK %s exception: %s", method, e)
        return {"error": {"error_msg": str(e)}}


def get_lp_server(token, group_id):
    r = vk_call("groups.getLongPollServer", {"group_id": group_id}, token)
    if "error" in r:
        logger.error("getLongPollServer error: %s", r["error"].get("error_msg"))
        return None
    return r.get("response")


def lp_poll(server, key, ts, wait=90):
    try:
        r = requests.get(server, params={
            "act": "a_check", "key": key, "ts": ts,
            "wait": wait, "mode": 2, "version": 3,
        }, timeout=wait + 10)
        return r.json()
    except Exception as e:
        logger.warning("LongPoll error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Парсер команд
# ---------------------------------------------------------------------------
def parse_command(text):
    if not text:
        return None
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.lower().split()
    cmd = parts[0]
    args = parts[1:] if len(parts) > 1 else []
    if cmd == "/help":
        return ("help", None)
    if cmd == "/status":
        return ("status", None)
    if cmd == "/stats":
        period = args[0] if args else "day"
        return ("stats", period if period in ("day", "week", "month") else "day")
    return None


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------
def _int(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def _to_date_str(raw):
    if raw is None or raw == "":
        return ""
    s = str(raw).strip()
    if "-" in s:
        return s[:10]
    if s.isdigit():
        try:
            ts = int(s)
            if ts > 10_000_000_000:
                ts = ts // 1000
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            return s
    return s


def _fmt_date_full(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return date_str


def _net_str(net):
    return f"+{net}" if net >= 0 else str(net)


# ---------------------------------------------------------------------------
# Сбор данных
# ---------------------------------------------------------------------------
def fetch_stats_for_group(group_id, token, days):
    data = vk_call("stats.get", {
        "group_id": abs(int(group_id)),
        "interval": "day",
        "intervals_count": days,
        "stats_groups": "visitors,reach,activity",
    }, token)

    if "error" in data:
        logger.warning("stats.get error %s: %s",
                       group_id, data["error"].get("error_msg"))
        return None

    items = data.get("response", [])
    if not isinstance(items, list):
        return None

    daily = []
    for it in items:
        date = _to_date_str(it.get("period_from") or it.get("period_to"))
        r = it.get("reach") or {}
        v = it.get("visitors") or {}
        a = it.get("activity") or {}

        reach_total = _int(r.get("reach")) or _int(r.get("reach_total"))
        reach_sub = _int(r.get("reach_subscribers"))
        reach_viral = _int(r.get("reach_viral"))
        views = _int(r.get("views")) or _int(v.get("views"))

        daily.append({
            "date": date,
            "reach": reach_total,
            "reach_subscribers": reach_sub,
            "reach_viral": reach_viral,
            "views": views,
            "subscribed": _int(a.get("subscribed")),
            "unsubscribed": _int(a.get("unsubscribed")),
        })

    members = 0
    real_name = None
    g = vk_call("groups.getById", {
        "group_id": abs(int(group_id)),
        "fields": "members_count",
    }, token)
    if "response" in g:
        resp = g["response"]
        grp = None
        if isinstance(resp, list) and resp:
            grp = resp[0]
        elif isinstance(resp, dict):
            grps = resp.get("groups", [])
            if grps:
                grp = grps[0]
        if grp:
            members = _int(grp.get("members_count"))
            real_name = grp.get("name")

    return {"real_name": real_name, "members": members, "daily": daily}


# ---------------------------------------------------------------------------
# Агрегация
# ---------------------------------------------------------------------------
_KEYS = ("reach", "reach_subscribers", "reach_viral",
         "views", "subscribed", "unsubscribed")


def _sum_daily(daily):
    total = {k: 0 for k in _KEYS}
    for d in daily:
        for k in _KEYS:
            total[k] += d.get(k, 0)
    return total


# ---------------------------------------------------------------------------
# Форматирование
# ---------------------------------------------------------------------------
def _block(s, members):
    """
    Вертикальный блок метрик.

    s — словарь сумм за период: reach, reach_viral, views, subscribed, unsubscribed
    members — общее число подписчиков группы (на момент запроса)
    """
    net = s["subscribed"] - s["unsubscribed"]
    return (
        f"📖Охват {s['reach']}\n"
        f"🔀Вирал {s['reach_viral']}\n"
        f"▶️Просм {s['views']}\n"
        f"\n"
        f"👥Подписчики Всего {members}\n"
        f"👤Прирост {_net_str(net)}"
    )


def format_stats_report(period, groups_data):
    days = {"day": 1, "week": 7, "month": 30}.get(period, 1)
    label = {"day": "день", "week": "неделю", "month": "месяц"}.get(period, period)

    all_dates = sorted({
        d["date"]
        for g in groups_data.values()
        for d in g["daily"]
        if d.get("date")
    })

    if not all_dates:
        period_str = "—"
    elif len(all_dates) == 1:
        period_str = _fmt_date_full(all_dates[0])
    else:
        period_str = (f"{_fmt_date_full(all_dates[0])} — "
                      f"{_fmt_date_full(all_dates[-1])}")

    lines = [
        f"📊 Статистика за {label}",
        f"🗓 {period_str}",
        "",
    ]

    # ---- Общий итог ----
    grand_total = {k: 0 for k in _KEYS}
    grand_members = 0
    for g in groups_data.values():
        t = _sum_daily(g["daily"])
        for k in _KEYS:
            grand_total[k] += t[k]
        grand_members += g.get("members", 0)

    lines.append("━━━ ОБЩАЯ ━━━")
    lines.append(_block(grand_total, grand_members))
    lines.append("")

    # ---- По группам ----
    for gid, g in groups_data.items():
        name = g.get("real_name") or f"Группа {gid}"
        link = f"vk.com/club{abs(int(gid))}"
        total = _sum_daily(g["daily"])
        members = g.get("members", 0)

        lines.append(f"🔹 {name}")
        lines.append(f"🔗 {link}")
        lines.append("")
        lines.append(_block(total, members))
        lines.append("")

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Обработчики команд
# ---------------------------------------------------------------------------
def handle_command(cmd, token, peer_id):
    cmd_type, arg = cmd

    if cmd_type == "help":
        send_alert(
            "🤖 StatsBots — команды:\n"
            "/stats day — за вчера\n"
            "/stats week — за 7 дней\n"
            "/stats month — за 30 дней\n"
            "/status — состояние\n"
            "/help — эта справка\n"
            "\n"
            "📖 Расшифровка метрик:\n"
            "📖Охват — уникальные пользователи\n"
            "🔀Вирал — из рекомендаций\n"
            "▶️Просм — все показы\n"
            "👥Подписчики — всего в группе\n"
            "👤Прирост — подписались − отписались",
            prefix=None,
        )
        return

    if cmd_type == "status":
        try:
            from core.busy import is_busy
            from core.db import get_queue_count
            busy = "занят" if is_busy() else "свободен"
            q = get_queue_count(processed=0)
            send_alert(f"ℹ️ Оркестратор: {busy}\nОчередь: {q}", prefix=None)
        except Exception as e:
            send_alert(f"⚠️ Ошибка status: {e}", prefix=None)
        return

    if cmd_type == "stats":
        period = arg or "day"
        days = {"day": 1, "week": 7, "month": 30}.get(period, 1)

        groups = get_active_groups()
        if not groups:
            send_alert("⚠️ Нет активных групп", prefix=None)
            return

        logger.info("Collecting stats period=%s groups=%d", period, len(groups))

        result = {}
        for g in groups:
            gid = g["group_id"]
            tok = get_group_token(gid) or token
            d = fetch_stats_for_group(gid, tok, days)
            if d:
                result[gid] = d
                logger.info("  group %s: name=%r days=%d members=%d",
                            gid, d.get("real_name"), len(d.get("daily", [])),
                            d.get("members", 0))
            else:
                logger.warning("  group %s: no data", gid)
            time.sleep(0.3)

        if not result:
            send_alert("⚠️ Не удалось получить статистику ни по одной группе",
                       prefix=None)
            return

        report = format_stats_report(period, result)
        send_alert(report, prefix=None)
        logger.info("Report sent period=%s groups=%d len=%d",
                    period, len(result), len(report))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    init_dbs()
    token = (os.getenv("VK_GROUP_TOKEN") or "").strip()
    gid_raw = (os.getenv("VK_GROUP_ID") or "").strip()
    admin_raw = (os.getenv("VK_ADMIN_ID") or "").strip()

    if not token:
        logger.error("VK_GROUP_TOKEN отсутствует")
        return
    if not gid_raw.isdigit():
        logger.error("VK_GROUP_ID не число: %r", gid_raw)
        return
    if not admin_raw.isdigit():
        logger.error("VK_ADMIN_ID не число")
        return

    group_id = int(gid_raw)
    admin_id = int(admin_raw)

    lp = get_lp_server(token, group_id)
    if not lp:
        logger.error("LongPoll недоступен. Проверь права messages и LongPoll API.")
        return

    server, key, ts = lp["server"], lp["key"], lp["ts"]
    logger.info("vk_listener started. server=%s group=%s admin=%s",
                server, group_id, admin_id)

    fail = 0
    while True:
        try:
            resp = lp_poll(server, key, ts)
            if not resp:
                fail += 1
                time.sleep(2)
                if fail > 5:
                    lp = get_lp_server(token, group_id)
                    if lp:
                        server, key, ts = lp["server"], lp["key"], lp["ts"]
                        fail = 0
                continue

            if "failed" in resp:
                if resp["failed"] == 1:
                    ts = resp["ts"]
                else:
                    lp = get_lp_server(token, group_id)
                    if lp:
                        server, key, ts = lp["server"], lp["key"], lp["ts"]
                continue

            ts = resp.get("ts", ts)
            for upd in resp.get("updates", []):
                if upd.get("type") != "message_new":
                    continue
                msg = upd.get("object", {}).get("message", {})
                if msg.get("from_id") != admin_id:
                    continue
                cmd = parse_command(msg.get("text", ""))
                if not cmd:
                    continue
                logger.info("Command: %s from=%s", cmd, admin_id)
                try:
                    handle_command(cmd, token, admin_id)
                except Exception as e:
                    logger.error("handle_command error: %s", e, exc_info=True)
                    send_alert(f"🚨 Ошибка команды: {e}", prefix=None)

        except KeyboardInterrupt:
            logger.info("Stopped by user")
            return
        except Exception as e:
            logger.error("loop error: %s", e, exc_info=True)
            time.sleep(3)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("vk_listener stopped")
        sys.exit(0)
