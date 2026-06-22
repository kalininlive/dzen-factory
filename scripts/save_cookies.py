"""
Ручная авторизация в Яндекс Дзен + сохранение cookies.

Запуск:
    cd /opt/dzen-publisher
    source publisher/.venv/bin/activate
    python scripts/save_cookies.py

На VPS без дисплея:
    sudo apt install xvfb -y
    Xvfb :99 -screen 0 1280x900x24 &
    export DISPLAY=:99
    python scripts/save_cookies.py

Что делает скрипт:
    1. Открывает браузер в HEADED режиме (виден пользователю)
    2. Переходит на https://dzen.ru/login
    3. Ждёт 120 секунд — пользователь вводит логин/пароль/OTP вручную
    4. Сохраняет все cookies в publisher/cookies/dzen_cookies.json
    5. Проверяет валидность сессии через API
"""

import asyncio
import json
import sys
from pathlib import Path

# Добавляем publisher/ в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent / "publisher"))

from patchright.async_api import async_playwright
from cookies import save_cookies
from config import DZEN_COOKIES_PATH

WAIT_SECONDS = 120
LOGIN_URL = "https://dzen.ru/login"
PROFILE_API = "https://dzen.ru/api/v2/profile"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def main() -> None:
    print("=" * 60)
    print("Яндекс Дзен — первичная авторизация")
    print("=" * 60)
    print(f"Cookies будут сохранены в: {DZEN_COOKIES_PATH}")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # HEADED — нужен дисплей
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        page = await ctx.new_page()

        print(f"Открываем {LOGIN_URL} ...")
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        print()
        print(f"У вас есть {WAIT_SECONDS} секунд.")
        print("Войдите в аккаунт Яндекс вручную (логин + пароль + OTP).")
        print("После входа убедитесь, что вы видите главную страницу Дзена.")
        print()

        for remaining in range(WAIT_SECONDS, 0, -10):
            print(f"  Осталось: {remaining} сек...")
            await asyncio.sleep(10)

        print()
        print("Сохраняем cookies...")
        cookies = await ctx.cookies()
        save_cookies(DZEN_COOKIES_PATH, cookies)
        print(f"Сохранено {len(cookies)} cookies → {DZEN_COOKIES_PATH}")

        # Проверка сессии
        print()
        print("Проверяем сессию...")
        try:
            resp = await page.request.get(PROFILE_API, headers={"Accept": "application/json"})
            if resp.status == 200:
                data = await resp.json()
                login = data.get("login") or data.get("uid")
                if login:
                    print(f"✅ Сессия валидна! Аккаунт: {login}")
                else:
                    print("⚠️  Сессия может быть невалидна (нет поля login в ответе API)")
            else:
                print(f"⚠️  API вернул статус {resp.status} — возможно, сессия невалидна")
        except Exception as e:
            print(f"⚠️  Не удалось проверить сессию: {e}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
