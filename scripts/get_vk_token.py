#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ручной запуск получения VK-токена.

Использование:
    python scripts/get_vk_token.py           # получить токен (при необходимости — логин)
    python scripts/get_vk_token.py --force   # игнорировать .env, всегда новый
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.vk_auth import get_valid_token, has_browser_profile


if __name__ == "__main__":
    force = "--force" in sys.argv

    if not has_browser_profile():
        print("⚠️  Профиль браузера пуст — откроется окно для первого входа.")

    token = get_valid_token(
        headless=False,
        force=force,
        interactive_ok=True,
    )
    if token:
        print(f"OK, token: {token[:20]}...")
    else:
        print("FAIL: не удалось получить токен")
        sys.exit(1)
