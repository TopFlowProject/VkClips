import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT_DIR / "data"
TEMP_DIR = ROOT_DIR / "temp"
LOGS_DIR = ROOT_DIR / "logs"
MUSIC_DIR = ROOT_DIR / "music"

DB_USED = DATA_DIR / "used_videos.db"
DB_QUEUE = DATA_DIR / "queue.db"
DB_GROUPS = DATA_DIR / "groups.db"

# ================== YouTube ==================
MAX_VIDEOS_PER_HASHTAG = 500
BATCH_TARGET = 150
MIN_VIEWS = 3000
MAX_VIEWS = 300000
MIN_LIKES_RATIO = 0.01
MIN_DURATION = 5
MAX_DURATION = 165
MIN_MONTHS_AGO = 0
MAX_MONTHS_AGO = 12
DAYS_TO_KEEP = 14

# ================== Очередь ==================
TARGET_VIDEOS = 90
SLEEP_LOADER = 60

# ================== Постинг ==================
GLOBAL_POST_INTERVAL = 300        # 5 минут между любыми постами
GROUP_COOLDOWN = 7200             # 2 часа между постами в одну группу
DAILY_LIMIT_DEFAULT = 1           # постов в день на группу

# ================== Дополнительные настройки ==================
RETRY_COUNT = 1
SLEEP_BETWEEN_HASHTAGS = 10
BATCH_SIZE = 50

# ================== API ==================
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN", "")
VK_API_VERSION = "5.131"
YT_API_KEY = os.getenv("YT_API_KEY", "")

# ================== Музыка ==================
MUSIC_FILES = list(MUSIC_DIR.glob("*.mp3")) if MUSIC_DIR.exists() else []

# ================== Прокси ==================
PROXY_POOL_MIN_SIZE = 15
PROXY_CHECK_INTERVAL = 30
TIMEOUT_REQUESTS = 10
MAX_LATENCY_MS = 12000
CHECK_URL = 'https://www.youtube.com'
ADVANCED_TYPES = ['socks5']
TOPROXYLAB_PROTOCOLS = ['socks5']
GITHUB_URLS = [
    # SOCKS5
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt',
    'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt',
    'https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt',
    'https://raw.githubusercontent.com/stormsia/proxy-list/main/socks5.txt',
    'https://raw.githubusercontent.com/Ian-Lusule/Proxies/main/proxies/socks5.txt',
    # HTTP/HTTPS
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
    'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/https.txt',
    'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt',
    'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt',
    'https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTP_RAW.txt',
    'https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt',
    # Дополнительные популярные списки
    'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/raw/proxies.txt',
    'https://raw.githubusercontent.com/proxylist-to/proxylist/main/socks5.txt',
    'https://raw.githubusercontent.com/mertguvencli/http-proxy-list/main/proxy-list/data.txt',
    'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt',
    'https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt',
    'https://raw.githubusercontent.com/ManuKraft/Proxy-List/main/socks5.txt',
]