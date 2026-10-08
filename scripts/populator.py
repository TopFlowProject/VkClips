#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.config import TARGET_VIDEOS, LOGS_DIR
from core.db import init_dbs, get_queue_count, add_to_queue
from core.youtube import collect_and_filter

LOG_FILE = LOGS_DIR / "populator.logging"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        TimedRotatingFileHandler(LOG_FILE, when="midnight", backupCount=14, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("populator")


def main():
    init_dbs()
    logger.info("Populator started")
    print("[DEBUG] Populator started")  # <-- добавил

    while True:
        try:
            count = get_queue_count(processed=0)
            print(f"[DEBUG] Queue count: {count}")  # <-- добавил

            if count < TARGET_VIDEOS:
                needed = TARGET_VIDEOS - count
                print(f"[DEBUG] Need {needed} videos, calling collect_and_filter...")  # <-- добавил

                videos = collect_and_filter(needed * 2)
                print(f"[DEBUG] collect_and_filter returned {len(videos)} videos")  # <-- добавил

                if videos:
                    for v in videos:
                        add_to_queue(v)
                    logger.info(f"Added {len(videos)} videos to queue")
                    print(f"[DEBUG] Added {len(videos)} videos to queue")  # <-- добавил
                else:
                    logger.error("No new videos found")
                    print("[DEBUG] No new videos found")  # <-- добавил

            cnt = get_queue_count(processed=0)
            print(f"[DEBUG] Queue count after processing: {cnt}")  # <-- добавил

            if cnt < 20:
                sleep_min = 15
            elif cnt < 50:
                sleep_min = 60
            else:
                sleep_min = 240
            print(f"[DEBUG] Sleeping for {sleep_min} minutes")  # <-- добавил
            time.sleep(sleep_min * 60)
        except Exception as e:
            logger.error(f"Populator error: {e}")
            print(f"[DEBUG] EXCEPTION: {e}")  # <-- добавил
            import traceback
            traceback.print_exc()  # <-- добавил
            time.sleep(60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        sys.exit(0)