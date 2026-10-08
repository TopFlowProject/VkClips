#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import random
import logging
from logging.handlers import TimedRotatingFileHandler
import requests
import re
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.config import (
    TEMP_DIR,
    LOGS_DIR,
    VK_API_VERSION,
)
from core.db import (
    init_dbs,
    get_next_video,
    mark_as_processed,
    mark_as_error,
    get_available_group,
    update_group_last_posted,
    get_group_token,
    get_queue_count,
)
from core.processor import process_video
from core.busy import set_busy, clear_busy
from core.vk_auth import is_token_alive, force_refresh_token
from core.account_guard import (
    is_blocked,
    mark_blocked,
    blocked_until_str,
    posts_today,
    is_daily_limit_reached,
    seconds_until_tomorrow,
    DAILY_LIMIT,
    BLOCK_DAYS,
)

# ---------- НАСТРОЙКИ ----------
VK_CLIENT_ID = 6287487
PAUSE_MIN = 600        # 10 минут
PAUSE_MAX = 1600       # ~27 минут

# ---------- LOGGING ----------
LOG_FILE = LOGS_DIR / "loader.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        TimedRotatingFileHandler(LOG_FILE, when="midnight", backupCount=14, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("loader")


class VKBlockedError(Exception):
    """VK ответил «user is blocked» — аккаунт забанен для публикации клипов."""
    pass


def clean_temp():
    for f in TEMP_DIR.glob("*"):
        if f.suffix in (".part", ".tmp"):
            try:
                f.unlink()
            except PermissionError:
                logger.warning(f"File busy, skipping: {f.name}")
            except Exception:
                pass


def clean_temp_force():
    for f in TEMP_DIR.glob("*"):
        if f.is_file():
            try:
                f.unlink()
            except PermissionError:
                logger.warning(f"File busy, skipping: {f.name}")
            except Exception:
                pass
    logger.info("Temp folder cleaned")


def extract_hashtags(text):
    if not text:
        return ""
    hashtags = re.findall(r'#\S+', text)
    return " ".join(hashtags) if hashtags else ""


# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ЗАГРУЗКИ ----------
def upload_multipart(video_path, upload_url, field_name):
    try:
        with open(video_path, "rb") as f:
            files = {field_name: f}
            resp = requests.post(upload_url, files=files, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "*/*",
            })
        logger.info(f"multipart ({field_name}) status: {resp.status_code}")
        if resp.status_code == 200 and 'application/json' in resp.headers.get('content-type', ''):
            resp_json = resp.json()
            if "error" not in resp_json:
                return True, None
            else:
                logger.error(f"Upload error: {resp_json['error']['error_msg']}")
                return False, None
        elif resp.status_code in (400, 412):
            return False, "renew"
        else:
            return False, None
    except Exception as e:
        logger.error(f"upload_multipart exception: {e}")
        return False, None


def upload_raw(video_path, upload_url):
    try:
        with open(video_path, "rb") as f:
            file_data = f.read()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(file_data)),
            "Accept": "*/*",
        }
        resp = requests.post(upload_url, data=file_data, headers=headers)
        logger.info(f"raw upload status: {resp.status_code}")
        if resp.status_code == 200 and 'application/json' in resp.headers.get('content-type', ''):
            resp_json = resp.json()
            if "error" not in resp_json:
                return True, None
            else:
                logger.error(f"Upload error: {resp_json['error']['error_msg']}")
                return False, None
        elif resp.status_code in (400, 412):
            return False, "renew"
        else:
            return False, None
    except Exception as e:
        logger.error(f"upload_raw exception: {e}")
        return False, None


# ---------- ОСНОВНАЯ ФУНКЦИЯ ЗАГРУЗКИ КЛИПА ----------
def upload_clip_to_vk(video_path, title, description, group_id, token):
    """
    Загружает клип через shortVideo.create.
    При «user is blocked» — сразу помечает аккаунт и кидает VKBlockedError.
    """
    logger.info(f"Starting VK upload (shortVideo.create): video={video_path.name}, group={group_id}")
    if not video_path.exists():
        logger.error(f"File not found: {video_path}")
        return None, None

    file_size = video_path.stat().st_size
    if file_size > 2 * 1024 ** 3:
        logger.error(f"File too large: {file_size} bytes (max 2GB)")
        return None, None

    clean_group_id = abs(int(group_id))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    create_url = "https://api.vk.com/method/shortVideo.create"
    params_create = {
        "access_token": token,
        "v": "5.285",
        "client_id": VK_CLIENT_ID,
        "group_id": clean_group_id,
        "file_size": file_size,
        "description": description[:200] if description else "",
        "wallpost": 1,
        "wall_post": 1,
    }

    upload_url = None
    video_id = None
    blocked_detected = False

    for attempt in range(3):
        try:
            logger.info(f"Sending shortVideo.create request (attempt {attempt+1}/3) with client_id={VK_CLIENT_ID}...")
            resp_create = requests.post(create_url, data=params_create, headers=headers)
            if 'application/json' not in resp_create.headers.get('content-type', ''):
                logger.error(f"Create response not JSON: {resp_create.text[:500]}")
                time.sleep(2)
                continue
            resp_create_json = resp_create.json()
            if "error" in resp_create_json:
                err_msg = resp_create_json['error']['error_msg']
                logger.error(f"Create error: {err_msg}")

                # КРИТИЧНО: не ретраим, помечаем аккаунт и выходим
                if "user is blocked" in err_msg.lower():
                    logger.error("VK: user is blocked — аккаунт заблокирован для публикации.")
                    blocked_detected = True
                    break

                if "Unknown method" in err_msg:
                    logger.error("This token doesn't support shortVideo.create. Try using video.save instead.")
                time.sleep(2)
                continue

            upload_url = resp_create_json.get("response", {}).get("upload_url")
            video_id = resp_create_json.get("response", {}).get("video_id")
            if upload_url and video_id:
                logger.info(f"Session created. Video ID: {video_id}")
                break
            else:
                logger.error("No upload_url or video_id in response")
                time.sleep(2)
        except Exception as e:
            logger.error(f"Create exception: {e}")
            time.sleep(2)

    if blocked_detected:
        # Пометим аккаунт на 5 дней и пробросим исключение наверх
        mark_blocked(days=BLOCK_DAYS)
        raise VKBlockedError("user is blocked")

    if not upload_url or not video_id:
        logger.error("Failed to obtain upload URL")
        return None, None

    # Загрузка файла — три стратегии
    upload_success = False
    strategies = [
        {"name": "multipart (field: video_file)", "func": lambda: upload_multipart(video_path, upload_url, "video_file")},
        {"name": "multipart (field: file)", "func": lambda: upload_multipart(video_path, upload_url, "file")},
        {"name": "raw binary with Content-Length", "func": lambda: upload_raw(video_path, upload_url)},
    ]

    for strategy in strategies:
        logger.info(f"Trying upload strategy: {strategy['name']}")
        for attempt in range(2):
            try:
                success, new_upload_url = strategy['func']()
                if success:
                    upload_success = True
                    logger.info(f"Upload succeeded with strategy: {strategy['name']}")
                    break
                elif new_upload_url == "renew":
                    logger.info("Session expired, renewing...")
                    resp_create = requests.post(create_url, data=params_create, headers=headers)
                    if 'application/json' in resp_create.headers.get('content-type', ''):
                        resp_create_json = resp_create.json()
                        if "error" not in resp_create_json:
                            upload_url = resp_create_json.get("response", {}).get("upload_url")
                            video_id = resp_create_json.get("response", {}).get("video_id")
                            logger.info(f"New upload_url obtained, video_id={video_id}")
                            continue
                else:
                    logger.warning(f"Strategy {strategy['name']} failed, attempt {attempt+1}/2")
            except Exception as e:
                logger.error(f"Strategy {strategy['name']} exception: {e}")
            time.sleep(2)
        if upload_success:
            break

    if not upload_success:
        logger.error("All upload strategies failed")
        return None, None

    # Ожидание кодирования
    progress_url = "https://api.vk.com/method/shortVideo.encodeProgress"
    params_progress = {
        "access_token": token,
        "v": "5.285",
        "owner_id": -clean_group_id,
        "video_id": video_id,
    }
    start = time.time()
    logger.info("Waiting for VK encoding (max 5 min)...")
    while time.time() - start < 300:
        try:
            pr = requests.post(progress_url, data=params_progress, headers=headers)
            if 'application/json' in pr.headers.get('content-type', ''):
                pr_json = pr.json()
                if "error" in pr_json and pr_json["error"].get("error_code") == 100:
                    pass
                elif "response" in pr_json:
                    percents = pr_json["response"].get("percents", 0)
                    ready = pr_json["response"].get("is_ready", False)
                    if percents >= 100 or ready:
                        logger.info(f"Encoding done: {percents}%")
                        break
            else:
                logger.warning(f"Progress response not JSON: {pr.text[:100]}")
        except Exception as e:
            logger.warning(f"Progress check error: {e}")
        time.sleep(15)

    # Публикация
    publish_url = "https://api.vk.com/method/shortVideo.publish"
    params_publish = {
        "access_token": token,
        "v": "5.285",
        "owner_id": -clean_group_id,
        "video_id": video_id,
        "wallpost": 1,
        "wall_post": 1,
        "license_agree": 1,
        "license_agreement": "true",
    }
    try:
        logger.info("Publishing clip...")
        resp_publish = requests.post(publish_url, data=params_publish, headers=headers)
        if 'application/json' in resp_publish.headers.get('content-type', ''):
            resp_publish_json = resp_publish.json()
            if "error" in resp_publish_json:
                err_msg = resp_publish_json['error']['error_msg']
                logger.error(f"Publish error: {err_msg}")
                if "user is blocked" in err_msg.lower():
                    logger.error("VK: user is blocked (publish).")
                    mark_blocked(days=BLOCK_DAYS)
                    raise VKBlockedError("user is blocked (publish)")
                return None, None
            else:
                logger.info(f"Clip published successfully, video_id={video_id}")
                return video_id, clean_group_id
        else:
            logger.error(f"Publish response not JSON: {resp_publish.text[:500]}")
    except VKBlockedError:
        raise
    except Exception as e:
        logger.error(f"Publish exception: {e}")

    return video_id, clean_group_id


# ---------- СКАЧИВАНИЕ ЧЕРЕЗ YT-DLP ----------
def download_with_ytdlp(video_url, output_path, proxy=None):
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp not installed. Run: pip install yt-dlp")
        return False

    ydl_opts = {
        'outtmpl': str(output_path),
        'quiet': True,
        'no_warnings': True,
        'format': 'mp4',
        'merge_output_format': 'mp4',
        'socket_timeout': 60,
        'retries': 10,
        'fragment_retries': 10,
        'skip_download': False,
        'ignoreerrors': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'skip': ['hls', 'dash'],
            }
        },
        'geo_bypass': True,
        'geo_bypass_country': 'RU',
        'user_agent': 'Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.115 Mobile Safari/537.36',
    }
    if proxy:
        ydl_opts['proxy'] = proxy
        logger.info(f"Using proxy: {proxy}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        return output_path.exists() and output_path.stat().st_size > 0
    except Exception as e:
        logger.error(f"yt-dlp download failed: {type(e).__name__}: {e!r}")
        return False


def download_video(video_url, output_path, timeout=180):
    logger.info(f"Starting download: {video_url}")
    if download_with_ytdlp(video_url, output_path):
        return True
    logger.error("Download failed (no proxy fallback)")
    return False


def main():
    init_dbs()
    clean_temp()
    logger.info("Loader started.")
    logger.info(f"Queue count (unprocessed): {get_queue_count(processed=0)}")
    logger.info(f"Daily limit per account: {DAILY_LIMIT}")

    while True:
        # === NIGHT BREAK (injected by fix.py) ===
        import datetime as _dt, time as _time
        _now = _dt.datetime.now()
        _nb_start = _dt.time(3, 30)
        _nb_end = _dt.time(6, 0)
        if _nb_start <= _now.time() < _nb_end:
            _target = _dt.datetime.combine(_now.date(), _nb_end)
            if _target <= _now:
                _target += _dt.timedelta(days=1)
            _secs = (_target - _now).total_seconds()
            print(f'[night_break] сон до {_target} ({_secs/3600:.1f}ч)')
            _time.sleep(_secs)
        # === END NIGHT BREAK ===
        # ---- 1. Проверка блокировки VK ----
        blocked, secs_left = is_blocked()
        if blocked:
            hrs = secs_left / 3600
            logger.warning(
                f"VK-аккаунт в блокировке. Разблокировка: {blocked_until_str()} "
                f"(осталось ~{hrs:.1f}ч). Ждём."
            )
            time.sleep(min(secs_left, 3600))
            continue

        # ---- 2. Проверка суточного лимита ----
        if is_daily_limit_reached():
            secs = seconds_until_tomorrow()
            logger.info(
                f"Суточный лимит {DAILY_LIMIT} публикаций достигнут. "
                f"Пауза до завтра ({secs/3600:.1f}ч)."
            )
            time.sleep(min(secs, 3600))
            continue

        queue_count = get_queue_count(processed=0)
        logger.info(
            f"Iteration start. Unprocessed: {queue_count}, "
            f"Сегодня опубликовано: {posts_today()}/{DAILY_LIMIT}"
        )

        video = get_next_video()
        if not video:
            logger.info("No videos in queue. Waiting 60 seconds...")
            time.sleep(60)
            continue

        video_id = video["id"]
        url = video["url"]
        original_description = video.get("description", "")
        description = extract_hashtags(original_description)
        title = video.get("title", "")

        logger.info(f"Processing video: {video_id} (title: {title[:50]}...)")

        group = get_available_group()
        if not group:
            logger.info("No available groups. Waiting 10 minutes...")
            time.sleep(600)
            continue

        group_id = group["group_id"]
        group_name = group.get("name", "Unknown")
        logger.info(f"Selected group: {group_name} (ID: {group_id})")

        group_token = get_group_token(group_id)
        if not group_token:
            logger.error(f"No token for group {group_id}. Skipping...")
            time.sleep(60)
            continue

        output_path = TEMP_DIR / f"{video_id}.mp4"
        if output_path.exists():
            output_path.unlink()

        if not download_video(url, output_path):
            logger.error(f"Download failed for {video_id}")
            mark_as_error(video_id, "Download failed")
            if output_path.exists():
                output_path.unlink()
            clean_temp()
            continue

        unique_path = process_video(output_path)
        if unique_path:
            output_path.unlink()
            output_path = unique_path

        # ---- 3. Публикация с обработкой VKBlockedError ----
        set_busy()
        vk_id = None
        blocked_error = False
        try:
            group_token = get_group_token(group_id)

            if not is_token_alive(group_token):
                logger.warning("VK token dead before upload, refreshing...")
                new_token = force_refresh_token(headless=True)
                if new_token:
                    group_token = new_token
                    logger.info("VK token refreshed")
                else:
                    logger.error("Не удалось обновить VK-токен")

            vk_id, _ = upload_clip_to_vk(output_path, title, description, group_id, group_token)
        except VKBlockedError:
            logger.error(
                f"VK: аккаунт заблокирован для публикации. "
                f"Пауза на {BLOCK_DAYS} дней (до {blocked_until_str()})."
            )
            blocked_error = True
        finally:
            clear_busy()

        if blocked_error:
            if output_path.exists():
                output_path.unlink()
            clean_temp_force()
            # Следующая итерация цикла увидит is_blocked() и уйдёт в сон
            continue

        if not vk_id:
            logger.error(f"VK upload failed for {video_id}")
            mark_as_error(video_id, "VK upload failed")
            if output_path.exists():
                output_path.unlink()
            clean_temp()
            continue

        mark_as_processed(video_id, group_id)
        update_group_last_posted(group_id, int(time.time()))
        if output_path.exists():
            output_path.unlink()

        logger.info(f"Done: {video_id} -> group {group_id}")
        clean_temp_force()

        # ---- 4. Случайная пауза 600–1600 сек ----
        pause = random.randint(PAUSE_MIN, PAUSE_MAX)
        logger.info(f"Sleeping {pause} sec (~{pause//60} min)...")
        time.sleep(pause)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        sys.exit(0)
