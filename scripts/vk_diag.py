#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диагностика VK-токенов и LongPoll.

Проверяет:
  - каждый токен: users.get / groups.getById / messages.send
  - LongPoll сервер для VK_GROUP_TOKEN
"""

import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

V = "5.199"
API = "https://api.vk.com/method/"


def call(method, token, **p):
    p2 = dict(p); p2["access_token"] = token; p2["v"] = V
    try:
        return requests.get(API + method, params=p2, timeout=10).json()
    except Exception as e:
        return {"error": {"error_msg": f"exc: {e}"}}


def short(tok):
    return f"{tok[:12]}...{tok[-6:]}" if tok and len(tok) > 20 else "(empty)"


def test_user_token(name, token, admin_id):
    print(f"\n=== {name} ===")
    if not token:
        print("  ❌ пусто"); return
    print(f"  значение: {short(token)}")

    r = call("users.get", token)
    if "response" in r:
        u = r["response"][0]
        print(f"  ✅ users.get: id={u['id']} {u.get('first_name','')} {u.get('last_name','')}")
    else:
        print(f"  ❌ users.get: {r.get('error', {}).get('error_msg')}")

    r = requests.post(API + "messages.send", data={
        "access_token": token, "v": V,
        "peer_id": admin_id, "message": f"[diag] {name}", "random_id": 0,
    }, timeout=10).json()
    if "response" in r:
        print(f"  ✅ messages.send → message_id={r['response']}")
    else:
        e = r.get("error", {})
        print(f"  ❌ messages.send: [{e.get('error_code')}] {e.get('error_msg')}")


def test_group_token(token, group_id, peer_id):
    print(f"\n=== VK_GROUP_TOKEN ===")
    if not token:
        print("  ❌ пусто"); return
    print(f"  значение: {short(token)}")
    print(f"  group_id: {group_id}  peer_id: {peer_id}")

    r = call("groups.getById", token, group_id=group_id)
    if "response" in r:
        resp = r["response"]
        grp = resp[0] if isinstance(resp, list) else (resp.get("groups") or [{}])[0]
        print(f"  ✅ groups.getById: {grp.get('name')} (id={grp.get('id')})")
    else:
        print(f"  ❌ groups.getById: {r.get('error', {}).get('error_msg')}")

    r = call("groups.getLongPollServer", token, group_id=group_id)
    if "response" in r:
        lp = r["response"]
        print(f"  ✅ LongPoll server: {lp.get('server')}")
    else:
        print(f"  ❌ LongPoll: {r.get('error', {}).get('error_msg')}")
        print("     → включи LongPoll API в настройках сообщества")

    r = requests.post(API + "messages.send", data={
        "access_token": token, "v": V,
        "peer_id": peer_id, "message": "[diag] group token",
        "random_id": 0, "from_group": 1, "group_id": group_id,
    }, timeout=10).json()
    if "response" in r:
        print(f"  ✅ messages.send → message_id={r['response']}")
    else:
        e = r.get("error", {})
        print(f"  ❌ messages.send: [{e.get('error_code')}] {e.get('error_msg')}")
        print("     → напиши сообществу первое сообщение (окно 24ч)")


def main():
    admin_raw = os.getenv("VK_ADMIN_ID", "")
    if not admin_raw.isdigit():
        print("❌ VK_ADMIN_ID не задан"); return
    admin_id = int(admin_raw)

    peer_raw = os.getenv("VK_ALERT_PEER_ID") or admin_raw
    peer_id = int(peer_raw)

    gid_raw = os.getenv("VK_GROUP_ID", "")

    print(f"VK_ADMIN_ID       = {admin_id}")
    print(f"VK_ALERT_PEER_ID  = {peer_id}")
    print(f"VK_GROUP_ID       = {gid_raw or '(не задан)'}")
    print(f"VK_GROUP_TOKEN:   {'есть' if os.getenv('VK_GROUP_TOKEN') else 'НЕТ'}")
    print(f"VK_ACCESS_TOKEN:  {'есть' if os.getenv('VK_ACCESS_TOKEN') else 'нет'}")
    print(f"VK_USER_TOKEN:    {'есть' if os.getenv('VK_USER_TOKEN') else 'нет'}")

    if gid_raw.isdigit():
        test_group_token((os.getenv("VK_GROUP_TOKEN") or "").strip(),
                         int(gid_raw), peer_id)
    else:
        print("\n⚠️  VK_GROUP_ID не задан — пропускаю тест группы")

    test_user_token("VK_ACCESS_TOKEN", (os.getenv("VK_ACCESS_TOKEN") or "").strip(), peer_id)
    test_user_token("VK_USER_TOKEN",   (os.getenv("VK_USER_TOKEN") or "").strip(), peer_id)

    print("\n" + "=" * 60)
    print("НУЖНО: чтобы у VK_GROUP_TOKEN было ✅ на все три пункта.")
    print("Если messages.send ❌ → напиши сообществу любое сообщение")
    print("(команда /help) — откроется окно 24ч.")


if __name__ == "__main__":
    main()
