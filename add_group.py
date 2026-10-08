#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

load_dotenv()  # загружаем .env

from core.db import add_group, init_dbs

def main():
    init_dbs()

    name = "Подними Настроение"
    group_id = -239288273

    # Берем токен из переменной окружения (пользовательский токен)
    token = os.getenv("VK_USER_TOKEN") or os.getenv("VK_ACCESS_TOKEN")
    if not token:
        print("❌ Токен не найден в .env!")
        return

    daily_limit = 1

    add_group(name, group_id, token, daily_limit)
    print(f"✅ Группа '{name}' (ID: {group_id}) добавлена в БД.")

if __name__ == "__main__":
    main()