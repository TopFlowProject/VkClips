"""
Busy-флаг для синхронизации между процессами (loader vs token refresh).
Файл-лок: есть файл → основа занята.
"""
import time
from pathlib import Path

from config.config import DATA_DIR

BUSY_FILE = DATA_DIR / "loader_busy.lock"
STALE_SECONDS = 600  # если файл старше 10 минут — считаем устаревшим


def set_busy() -> None:
    BUSY_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUSY_FILE.write_text(str(int(time.time())), encoding="utf-8")


def clear_busy() -> None:
    try:
        BUSY_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def is_busy() -> bool:
    if not BUSY_FILE.exists():
        return False
    try:
        ts = int(BUSY_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    if time.time() - ts > STALE_SECONDS:
        # Устаревший лок (упал процесс) — снимаем
        clear_busy()
        return False
    return True


def wait_until_idle(poll_interval: int = 30, stop_event=None,
                    max_wait_seconds: int = 3600) -> bool:
    """
    Ждёт, пока busy-флаг снят. Возвращает True если дождались,
    False если stop_event или превышен max_wait_seconds.
    """
    start = time.time()
    while is_busy():
        if stop_event is not None and stop_event.is_set():
            return False
        if time.time() - start > max_wait_seconds:
            return False
        time.sleep(poll_interval)
    return True
