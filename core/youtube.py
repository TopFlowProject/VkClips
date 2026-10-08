"""
Модуль для сбора ID и фильтрации видео с YouTube.
Использует YouTube Data API, YouTube Data API и базу данных used_videos.
"""

import time
import random
import contextlib
import io
import requests
import yt_dlp
import datetime
import re
from pathlib import Path

from config.config import (
    ROOT_DIR,
    MAX_VIDEOS_PER_HASHTAG,
    MIN_VIEWS,
    MAX_VIEWS,
    MIN_LIKES_RATIO,
    MIN_DURATION,
    MAX_DURATION,
    MIN_MONTHS_AGO,
    MAX_MONTHS_AGO,
    YT_API_KEY,
    RETRY_COUNT,
    SLEEP_BETWEEN_HASHTAGS,
    BATCH_SIZE,
)
from core.db import is_video_used, add_to_used_videos


class DummyLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


# ---------- Вспомогательные функции ----------
def load_hashtags():
    hash_file = ROOT_DIR / "hash.txt"
    if not hash_file.exists():
        return []
    with open(hash_file, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def parse_duration(duration_str):
    try:
        import isodate
        return int(isodate.parse_duration(duration_str).total_seconds())
    except:
        match = re.match(r'PT(\d+H)?(\d+M)?(\d+S)?', duration_str)
        if not match:
            return 0
        hours = int(match.group(1)[:-1]) if match.group(1) else 0
        minutes = int(match.group(2)[:-1]) if match.group(2) else 0
        seconds = int(match.group(3)[:-1]) if match.group(3) else 0
        return hours * 3600 + minutes * 60 + seconds

def is_russian(text):
    if not text:
        return False
    return any(ord('а') <= ord(c) <= ord('я') or ord('А') <= ord(c) <= ord('Я') for c in text)

def get_video_ids(hashtag, retries=RETRY_COUNT):
    for attempt in range(retries):
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'playlistend': MAX_VIDEOS_PER_HASHTAG,
            'logger': DummyLogger(),
            'socket_timeout': 15,
        }
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f'https://www.youtube.com/hashtag/{hashtag}/shorts', download=False)
                    entries = info.get('entries', [])
                    ids = [e['id'] for e in entries if e and e.get('id')]
                    return ids
        except Exception:
            time.sleep(1)
    return []

def filter_videos(video_ids):
    if not YT_API_KEY or YT_API_KEY == "ваш_ключ":
        return []

    filtered = []
    for i in range(0, len(video_ids), BATCH_SIZE):
        batch_ids = video_ids[i:i+BATCH_SIZE]
        ids_str = ','.join(batch_ids)
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            'key': YT_API_KEY,
            'id': ids_str,
            'part': 'snippet,statistics,contentDetails',
        }
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                continue
            items = response.json().get('items', [])
            for item in items:
                video_id = item.get('id', '')
                if is_video_used(video_id):
                    continue

                snippet = item.get('snippet', {})
                statistics = item.get('statistics', {})
                content_details = item.get('contentDetails', {})

                views = int(statistics.get('viewCount', 0))
                if views < MIN_VIEWS:
                    continue
                if MAX_VIEWS is not None and views > MAX_VIEWS:
                    continue

                upload_date_str = snippet.get('publishedAt', '')[:10].replace('-', '')
                if not upload_date_str:
                    continue
                try:
                    upload_date = datetime.datetime.strptime(upload_date_str, '%Y%m%d').date()
                except:
                    continue

                today = datetime.date.today()
                min_date = today - datetime.timedelta(days=int(MIN_MONTHS_AGO * 30))
                max_date = today - datetime.timedelta(days=int(MAX_MONTHS_AGO * 30))
                if upload_date > min_date or upload_date < max_date:
                    continue

                likes = int(statistics.get('likeCount', 0))
                if views == 0:
                    continue
                ratio = likes / views
                if ratio < MIN_LIKES_RATIO:
                    continue

                duration_iso = content_details.get('duration', '')
                duration_sec = parse_duration(duration_iso)
                if duration_sec < MIN_DURATION or duration_sec > MAX_DURATION:
                    continue

                title = snippet.get('title', '')
                if not is_russian(title):
                    continue

                video_data = {
                    'id': video_id,
                    'url': f'https://youtube.com/shorts/{video_id}',
                    'views': views,
                    'upload_date': upload_date_str,
                    'title': title,
                    'duration': duration_sec,
                    'likes': likes,
                    'description': snippet.get('description', '').replace('\n', ' ').replace('\r', ' '),
                    'channel': snippet.get('channelTitle', ''),
                }
                filtered.append(video_data)
                add_to_used_videos(video_id)
        except Exception as e:
            continue
    return filtered

def collect_and_filter(target_count):

    hashtags = load_hashtags()

    if not hashtags:

        return []



    all_ids = []

    processed = set()



    while len(all_ids) < target_count * 2:

        available = [h for h in hashtags if h not in processed]

        if not available:

            break

        batch = random.sample(available, min(5, len(available)))

        for tag in batch:

            ids = get_video_ids(tag)

            if ids:

                all_ids.extend(ids)

                processed.add(tag)

            time.sleep(SLEEP_BETWEEN_HASHTAGS)

            if len(all_ids) >= target_count * 2:

                break



    videos = filter_videos(all_ids)

    return videos


