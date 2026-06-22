"""
dzen.py — РАБОЧАЯ ВЕРСИЯ (публикация текста подтверждена 2026-06-01)
Верифицировано: https://dzen.ru/a/ah1gvodczC6XegXq
Изображения: пока не работают (следующий этап)
"""

import asyncio
import json
import logging
import os
import random
import re
import tempfile
from typing import Optional

import httpx
from patchright.async_api import async_playwright, Browser, BrowserContext, Page

from cookies import load_cookies
from config import (
    DZEN_COOKIES_PATH,
    PROXY_URL,
    ACTION_DELAY_MIN,
    ACTION_DELAY_MAX,
)

log = logging.getLogger(__name__)

# ── Verified URLs (2026-05-30) ────────────────────────────────────────────────
ENTRY_URL = "https://dzen.ru/profile/editor/create"
PROFILE_API = "https://dzen.ru/api/v3/launcher/export"

# ── Verified selectors (2026-05-30) ──────────────────────────────────────────
ADD_BUTTON = '[data-testid="add-publication-button"]'
WRITE_ARTICLE_TEXT = "Написать статью"
TEXTBOX_SELECTOR = '[role="textbox"]'
PUBLISH_BUTTON = '[data-testid="article-publish-btn"]'   # шаг 1 — открывает боковую панель
SIDE_PUBLISH_BUTTON = '[data-testid="publish-btn"]'      # шаг 2 — финальная публикация
MODAL_SELECTOR = '[role="dialog"], .ReactModal__Content'

PUBLISHED_URL_RE = re.compile(r"https://dzen\.ru/a/[A-Za-z0-9_-]+")
EDITOR_URL_RE = re.compile(
    r"https://dzen\.ru/profile/editor/id/[^/]+/([A-Za-z0-9_-]+)/edit"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Exceptions ────────────────────────────────────────────────────────────────

class SessionExpiredError(Exception):
    pass


class CaptchaDetectedError(Exception):
    pass


class EditorNotFoundError(Exception):
    pass


class PublishError(Exception):
    pass


# ── Publisher ─────────────────────────────────────────────────────────────────

class DzenPublisher:
    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        return self

    async def __aexit__(self, *_):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ── Public ────────────────────────────────────────────────────────────────

    async def check_session(self) -> bool:
        cookies = load_cookies(DZEN_COOKIES_PATH)
        if not cookies:
            return False
        ctx = await self._make_context(cookies)
        try:
            page = await ctx.new_page()
            resp = await page.request.get(
                PROFILE_API,
                headers={"Accept": "application/json", "Referer": "https://dzen.ru/"},
            )
            if resp.status != 200:
                return False
            data = await resp.json()
            user_info = data.get("current_user_source_info")
            return bool(user_info and (user_info.get("id") or user_info.get("title")))
        except Exception:
            return False
        finally:
            await ctx.close()

    async def publish(
        self,
        title: str,
        body: str,
        image_urls: list[str] | None = None,
    ) -> dict:
        cookies = load_cookies(DZEN_COOKIES_PATH)
        if not cookies:
            return _err("session_expired", "Файл cookies не найден")

        ctx = await self._make_context(cookies)
        page = await ctx.new_page()
        try:
            return await self._do_publish(page, title, body, image_urls or [])
        except SessionExpiredError as e:
            return _err("session_expired", str(e))
        except CaptchaDetectedError as e:
            await _screenshot(page, "captcha_detected.png")
            return _err("captcha_detected", str(e))
        except EditorNotFoundError as e:
            await _screenshot(page, "editor_not_found.png")
            return _err("editor_not_found", str(e))
        except Exception as e:
            await _screenshot(page, "publish_error.png")
            return _err("publish_error", str(e))
        finally:
            await ctx.close()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _make_context(self, cookies: list) -> BrowserContext:
        proxy = None
        if PROXY_URL:
            from urllib.parse import urlparse
            _p = urlparse(PROXY_URL)
            proxy = {"server": f"{_p.scheme}://{_p.hostname}:{_p.port}"}
            if _p.username:
                proxy["username"] = _p.username
            if _p.password:
                proxy["password"] = _p.password
        ctx = await self._browser.new_context(
            proxy=proxy,
            viewport={"width": 1440, "height": 900},
            user_agent=USER_AGENT,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        await ctx.add_cookies(cookies)
        return ctx

    async def _do_publish(
        self, page: Page, title: str, body: str, image_urls: list[str]
    ) -> dict:
        log.info("Переход в Студию: %s", ENTRY_URL)
        await page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)

        if "login" in page.url or await page.query_selector("text=Войти"):
            raise SessionExpiredError("Сессия истекла — редирект на страницу входа")

        if await _is_captcha_page(page):
            raise CaptchaDetectedError("SmartCaptcha обнаружена")

        await self._close_modals(page)

        # Открываем меню создания
        log.info("Нажимаем кнопку создания публикации...")
        await page.wait_for_selector(ADD_BUTTON, timeout=15_000)
        await page.evaluate(
            "document.querySelector('[data-testid=\"add-publication-button\"]').click()"
        )
        await asyncio.sleep(2)

        # Выбираем "Написать статью"
        log.info("Выбираем 'Написать статью'...")
        article_menu_item = None
        for _sel in [
            f'text="{WRITE_ARTICLE_TEXT}"',
            f'text={WRITE_ARTICLE_TEXT}',
            f'[role="menuitem"]:has-text("{WRITE_ARTICLE_TEXT}")',
            f'li:has-text("{WRITE_ARTICLE_TEXT}")',
        ]:
            loc = page.locator(_sel).first
            try:
                await loc.wait_for(timeout=5_000)
                article_menu_item = loc
                break
            except Exception:
                pass
        if article_menu_item is None:
            log.warning("Меню не открылось, повторяем клик...")
            await page.evaluate(
                "document.querySelector('[data-testid=\"add-publication-button\"]').click()"
            )
            await asyncio.sleep(2)
            article_menu_item = page.locator(f'text={WRITE_ARTICLE_TEXT}').first
        await page.evaluate(
            """() => {
                const all = document.querySelectorAll('span, button, li, [role="menuitem"]');
                for (const el of all) {
                    if (el.innerText && el.innerText.trim() === 'Написать статью') {
                        el.click();
                        return;
                    }
                }
            }"""
        )
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(5)

        await self._close_modals(page)

        # Заполняем редактор — ТОЛЬКО ТЕКСТ (изображения пропускаем)
        await self._fill_editor_text_only(page, title, body)
        await self._close_modals(page)

        # Настраиваем перехват URL
        intercepted_url: list[str] = []

        def _on_navigate(frame) -> None:
            if frame == page.main_frame and PUBLISHED_URL_RE.match(frame.url):
                intercepted_url.append(frame.url)

        page.on("framenavigated", _on_navigate)

        # Шаг 1: article-publish-btn
        log.info("Нажимаем первую кнопку 'Опубликовать'...")
        await page.evaluate(
            "document.querySelector('[data-testid=\"article-publish-btn\"]').click()"
        )
        await asyncio.sleep(2)

        # Диагностика кнопок после открытия панели
        _panel_btns = await page.query_selector_all('button')
        _panel_info = []
        for _b in _panel_btns[:30]:
            _tid = await _b.get_attribute('data-testid') or ''
            _txt = (await _b.inner_text())[:40]
            if _tid or _txt.strip():
                _panel_info.append(f"testid={_tid!r} text={_txt.strip()!r}")
        log.info("Кнопки после открытия боковой панели: %s", '; '.join(_panel_info))

        # Шаг 2: publish-btn
        log.info("Нажимаем финальную кнопку 'Опубликовать'...")
        side_btn = None
        for _sel in [
            '[data-testid="publish-btn"]',
            '[data-testid="final-publish-btn"]',
            '[data-testid="publication-publish-btn"]',
            '[data-testid="submit-publish-btn"]',
        ]:
            try:
                await page.wait_for_selector(_sel, timeout=3_000)
                side_btn = page.locator(_sel).first
                log.info("Финальная кнопка найдена по: %s", _sel)
                break
            except Exception:
                pass
        if side_btn is None:
            _pub_btns = page.locator('button:has-text("Опубликовать")')
            _pub_count = await _pub_btns.count()
            log.info("Кнопок 'Опубликовать' на странице: %d", _pub_count)
            if _pub_count >= 2:
                side_btn = _pub_btns.nth(1)
            elif _pub_count == 1:
                side_btn = _pub_btns.first
        if side_btn:
            await side_btn.scroll_into_view_if_needed()
            await asyncio.sleep(0.3)
            await side_btn.click()
            log.info("Финальная кнопка нажата, ждём публикации...")

            await asyncio.sleep(1.5)
            if await _is_captcha_page(page):
                await _screenshot(page, "captcha_detected.png")
                raise CaptchaDetectedError(
                    "SmartCaptcha при публикации — настройте мобильный прокси (PROXY_URL в .env)"
                )
        else:
            log.warning("Финальная кнопка публикации не найдена")

        # Ждём URL
        try:
            await page.wait_for_url(PUBLISHED_URL_RE.pattern, timeout=20_000)
            published_url = page.url
        except Exception:
            if PUBLISHED_URL_RE.match(page.url):
                published_url = page.url
            elif intercepted_url:
                published_url = intercepted_url[-1]
                log.info("URL статьи перехвачен до редиректа: %s", published_url)
            else:
                await _screenshot(page, "publish_error.png")
                if await _is_captcha_page(page):
                    raise CaptchaDetectedError(
                        "SmartCaptcha при публикации — настройте мобильный прокси (PROXY_URL в .env)"
                    )
                raise PublishError(
                    f"URL статьи не получен. Текущий URL: {page.url}"
                )

        log.info("Опубликовано: %s", published_url)
        return {"success": True, "published_url": published_url}

    async def _fill_editor_text_only(self, page: Page, title: str, body: str) -> None:
        """Заполняет редактор только текстом, убирая маркеры [IMAGE_N]."""
        log.info("Заполняем редактор текстом...")
        textboxes = page.locator(TEXTBOX_SELECTOR)
        await asyncio.sleep(2)

        count = await textboxes.count()
        if count < 2:
            raise EditorNotFoundError(
                f"Найдено {count} полей ввода, ожидалось 2. URL: {page.url}"
            )

        await textboxes.first.click(force=True)
        await self._paste_text(page, title)
        await asyncio.sleep(0.5)

        clean_body = re.sub(r'\[IMAGE_\d+\]', '', body).strip()
        await textboxes.nth(1).click(force=True)
        await self._paste_text(page, clean_body)
        await asyncio.sleep(0.5)

    async def _paste_text(self, page: Page, text: str) -> None:
        await page.evaluate(
            """(text) => {
                const dt = new DataTransfer();
                dt.setData('text/plain', text);
                document.activeElement.dispatchEvent(
                    new ClipboardEvent('paste', {
                        clipboardData: dt,
                        bubbles: true,
                        cancelable: true
                    })
                );
            }""",
            text,
        )
        await asyncio.sleep(0.3)

    async def _close_modals(self, page: Page) -> None:
        await page.evaluate("""() => {
            const selectors = [
                '[data-testid*="close"]', '[data-testid*="cross"]',
                '[aria-label*="Закрыть"]',
                '.editor--donations-promo-banner-popup__closeButton-1o'
            ];
            selectors.forEach(s =>
                document.querySelectorAll(s).forEach(b => {
                    try { b.click(); } catch(e) {}
                })
            );
            const confirmTexts = ["Понятно", "Ок", "Хорошо", "Продолжить", "Закрыть"];
            document.querySelectorAll('button').forEach(b => {
                try {
                    if (confirmTexts.some(t => b.innerText && b.innerText.includes(t))) {
                        b.click();
                    }
                } catch(e) {}
            });
        }""")
        await asyncio.sleep(0.5)
        overlay = page.locator('[data-testid="modal-overlay"]')
        if await overlay.count() > 0:
            await page.keyboard.press('Escape')
            await asyncio.sleep(0.5)
        if await overlay.count() > 0:
            try:
                await overlay.first.click(force=True)
                await asyncio.sleep(0.5)
            except Exception:
                pass
        await asyncio.sleep(0.5)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _err(code: str, message: str) -> dict:
    return {"success": False, "error": code, "message": message}


async def _is_captcha_page(page: Page) -> bool:
    if "smartcaptcha" in page.url:
        return True
    try:
        for sel in ['[class*="captcha"]', '[id*="captcha"]',
                    'iframe[src*="captcha"]', 'iframe[src*="smartcaptcha"]']:
            if await page.locator(sel).first.count() > 0:
                return True
        if await page.locator('text="Подтвердите, что вы не робот"').count() > 0:
            return True
    except Exception:
        pass
    return False


async def _screenshot(page: Page, filename: str) -> None:
    try:
        await page.screenshot(path=filename)
    except Exception:
        pass
