"""
Модуль автоматического получения/обновления VK access token.
"""
import re
import os
import time
import requests
from pathlib import Path

try:
    from DrissionPage import ChromiumPage, ChromiumOptions
    HAS_DRISSION = True
except ImportError:
    HAS_DRISSION = False

from config.config import ROOT_DIR, DATA_DIR

CLIENT_ID = os.getenv("VK_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("VK_CLIENT_SECRET", "")
REDIRECT_URI = "https://oauth.vk.ru/blank.html"
SCOPE = "video,wall,groups,photos,docs,offline"
V = "5.199"

PROFILE_DIR = DATA_DIR / "vk_browser_profile"
ENV_FILE = ROOT_DIR / ".env"

POLL_INTERVAL = 90


def read_env_var(var_name: str):
    env_path = Path(ENV_FILE)
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{var_name}="):
            return line.split("=", 1)[1].strip()
    return None


def save_env_var(var_name: str, var_value: str) -> None:
    env_path = Path(ENV_FILE)
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_line = f"{var_name}={var_value}\n"
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{var_name}="):
            lines[i] = new_line
            found = True
            break
    if not found:
        lines.append(new_line)
    env_path.write_text("".join(lines), encoding="utf-8")


def has_browser_profile() -> bool:
    try:
        return PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())
    except Exception:
        return False


def is_token_alive(token: str) -> bool:
    if not token:
        return False
    try:
        r = requests.get(
            "https://api.vk.com/method/users.get",
            params={"access_token": token, "v": V},
            timeout=5,
        ).json()
        return "response" in r
    except Exception:
        return False


def _open_browser(headless: bool):
    co = ChromiumOptions()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    co.set_user_data_path(str(PROFILE_DIR))
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")
    co.set_argument("--disable-blink-features=AutomationControlled")
    if headless:
        co.headless()
    return ChromiumPage(co)


def _is_logged_in(page) -> bool:
    try:
        page.get("https://vk.com/feed")
        time.sleep(2)
        return not ("login" in page.url or "auth" in page.url)
    except Exception:
        return False


def _try_click_continue(page) -> bool:
    for xpath in [
        'xpath://button[contains(., "Продолжить как")]',
        'xpath://button[contains(., "Продолжить")]',
        'xpath://button[contains(., "Разрешить")]',
        'xpath://*[@role="button"][contains(., "Продолжить")]',
    ]:
        try:
            btn = page.ele(xpath, timeout=0.3)
            if btn:
                btn.click()
                time.sleep(2)
                return True
        except Exception:
            continue
    return False


def _fetch_code_with_page(page) -> str | None:
    auth_url = (
        f"https://oauth.vk.ru/authorize?"
        f"client_id={CLIENT_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"scope={SCOPE}&"
        f"response_type=code&"
        f"v={V}"
    )
    page.get(auth_url)
    time.sleep(2.5)
    for _ in range(20):
        m = re.search(r"[?#]code=([^&]+)", page.url)
        if m:
            return m.group(1)
        _try_click_continue(page)
        time.sleep(0.5)
    return None


def get_code_headless() -> str | None:
    if not HAS_DRISSION:
        print("[vk_auth] DrissionPage не установлен")
        return None
    if not has_browser_profile():
        return None
    page = None
    try:
        page = _open_browser(headless=True)
        if not _is_logged_in(page):
            return None
        return _fetch_code_with_page(page)
    except Exception as e:
        print(f"[vk_auth] headless error: {e}")
        return None
    finally:
        if page:
            try:
                page.quit()
            except Exception:
                pass


def get_code_visible(wait_seconds: int = 3600) -> str | None:
    if not HAS_DRISSION:
        print("[vk_auth] DrissionPage не установлен")
        return None
    page = None
    try:
        page = _open_browser(headless=False)
        if not _is_logged_in(page):
            print()
            print("=" * 55)
            print("  Открылось окно браузера.")
            print("  Войди в аккаунт VK — я подожду автоматически.")
            print(f"  (максимум {wait_seconds} сек, но обычно быстрее)")
            print("=" * 55)
            page.get("https://vk.com/login")
            start = time.time()
            logged = False
            while time.time() - start < wait_seconds:
                time.sleep(POLL_INTERVAL)
                if _is_logged_in(page):
                    logged = True
                    print("[vk_auth] Вход обнаружен, продолжаю...")
                    break
            if not logged:
                print("[vk_auth] Таймаут ожидания логина")
                return None
        else:
            print("[vk_auth] Профиль уже авторизован")
        return _fetch_code_with_page(page)
    except Exception as e:
        print(f"[vk_auth] visible error: {e}")
        return None
    finally:
        if page:
            try:
                page.quit()
            except Exception:
                pass


def exchange_code_for_token(code: str) -> str | None:
    params = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }
    try:
        resp = requests.get(
            "https://oauth.vk.ru/access_token", params=params, timeout=15
        ).json()
    except Exception as e:
        print(f"[vk_auth] Ошибка обмена code: {e}")
        return None
    if "access_token" in resp:
        token = resp["access_token"]
        save_env_var("VK_ACCESS_TOKEN", token)
        print("[vk_auth] Новый токен сохранён в .env")
        return token
    print(f"[vk_auth] VK вернул ошибку: {resp}")
    return None


def get_valid_token(headless: bool = True, force: bool = False,
                    interactive_ok: bool = False) -> str | None:
    if not force:
        token = read_env_var("VK_ACCESS_TOKEN")
        if token and is_token_alive(token):
            return token
    if has_browser_profile():
        code = get_code_headless()
        if code:
            new_token = exchange_code_for_token(code)
            if new_token:
                return new_token
        print("[vk_auth] Headless обновление не удалось")
    if interactive_ok:
        print("[vk_auth] Открываю видимое окно для авторизации...")
        code = get_code_visible()
        if code:
            return exchange_code_for_token(code)
    else:
        print("[vk_auth] Видимое окно не разрешено (interactive_ok=False)")
    return None


def force_refresh_token(headless: bool = True) -> str | None:
    print("[vk_auth] Принудительное обновление токена...")
    if headless:
        code = get_code_headless()
    else:
        code = get_code_visible()
    if not code:
        print("[vk_auth] Не удалось получить code (сессия браузера истекла?)")
        return None
    return exchange_code_for_token(code)
