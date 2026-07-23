"""
Dzen browser automation via Patchright.
Selectors verified on live site 2026-05-30 by Gemini CLI.
Editor type: Draft.js (not ProseMirror).

Flow: title → text0 → image0 → text1 → image1 → text2
"""

import asyncio
import logging
import os
import re
import tempfile
from typing import Optional

import httpx
from patchright.async_api import async_playwright, Browser, BrowserContext, Page

from cookies import load_cookies
from config import (
    DZEN_COOKIES_PATH,
    PROXY_URL,
)

log = logging.getLogger(__name__)

# ── Verified URLs ─────────────────────────────────────────────────
ENTRY_URL = "https://dzen.ru/profile/editor/create"
PROFILE_API = "https://dzen.ru/api/v3/launcher/export"

# ── Verified selectors (2026-05-30) ──────────────────────────────────
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


# ── Exceptions ──────────────────────────────────────────────────────

class SessionExpiredError(Exception):
    pass


class CaptchaDetectedError(Exception):
    pass


class EditorNotFoundError(Exception):
    pass


class PublishError(Exception):
    pass


async def _is_login_page(page: Page) -> bool:
    url = page.url.lower()
    # 1. Проверяем явные URL авторизации Яндекса/Дзена
    if any(x in url for x in ["passport.yandex", "sso.dzen.ru", "dzen.ru/login", "oauth.yandex", "auth?retpath"]):
        return True
    # 2. Проверяем наличие специфических элементов формы входа Яндекса/Дзена
    if await page.query_selector('input[id="passp-field-login"], input[name="login"], input[type="password"]'):
        return True
    return False


# ── Publisher ───────────────────────────────────────────────────────

class DzenPublisher:
    def __init__(self, account_id: Optional[str] = None):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self.account_id = account_id

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

    def _get_cookies_path(self) -> str:
        if self.account_id:
            base_dir = os.path.dirname(DZEN_COOKIES_PATH)
            return os.path.join(base_dir, f"{self.account_id}.json")
        return DZEN_COOKIES_PATH

    # ── Public ────────────────────────────────────────────────────────

    async def check_session(self) -> bool:
        cookies = load_cookies(self._get_cookies_path())
        if not cookies:
            return False
        ctx = await self._make_context(cookies)
        try:
            page = await ctx.new_page()
            
            # 1. Сначала пробуем быстрый API запрос
            resp = await page.request.get(
                PROFILE_API,
                headers={"Accept": "application/json", "Referer": "https://dzen.ru/"},
            )
            if resp.status == 200:
                try:
                    data = await resp.json()
                    user_info = data.get("current_user_source_info")
                    if user_info and (user_info.get("id") or user_info.get("title")):
                        return True
                except Exception:
                    pass
            
            # 2. Если API вернул ошибку, заходим в браузере (Дзен API часто лагает)
            log.info("API проверка сессии не удалась. Проверяем через реальный переход в браузере...")
            await page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=30_000)
            
            if await _is_login_page(page):
                log.warning("Проверка сессии: обнаружен редирект на логин")
                return False
                
            # Если виден селектор кнопки публикации или мы находимся в редакторе, сессия валидна
            if await page.query_selector(ADD_BUTTON) or "editor" in page.url:
                log.info("Проверка сессии: сессия успешно подтверждена через веб-интерфейс")
                return True
                
            return False
        except Exception as e:
            log.warning("Ошибка при проверке сессии в dzen.py: %s", e)
            return False
        finally:
            await ctx.close()

    async def publish(
        self,
        content_type: str = "article",
        title: Optional[str] = None,
        body: Optional[str] = None,
        image_urls: list[str] | None = None,
        video_url: Optional[str] = None,
        cover_url: Optional[str] = None,
    ) -> dict:
        cookies = load_cookies(self._get_cookies_path())
        if not cookies:
            return _err("session_expired", "Файл cookies не найден")

        ctx = await self._make_context(cookies)
        page = await ctx.new_page()
        try:
            if content_type == "post":
                return await self._do_publish_post(page, body or "", image_urls or [])
            elif content_type == "reel":
                return await self._do_publish_reel(page, video_url or "", body or "")
            elif content_type == "video":
                return await self._do_publish_video(
                    page, video_url or "", title or "Без названия", body or "", cover_url
                )
            else:
                return await self._do_publish(page, title or "Без названия", body or "", image_urls or [])
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
            draft_url = page.url if EDITOR_URL_RE.search(page.url) else None
            return _err("publish_error", str(e), draft_url=draft_url)
        finally:
            await ctx.close()


    # ── Internal ──────────────────────────────────────────────────────

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
        await ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        return ctx

    async def _handle_channel_setup_modal(self, page: Page) -> bool:
        """Обрабатывает модальное окно настройки канала (согласие с правилами для новых авторов)
        Возвращает True, если окно настроек было обнаружено и успешно закрыто, иначе False.
        """
        try:
            # Проверяем наличие окна "Настройка канала" на странице
            has_modal = await page.evaluate("""() => {
                const textFound = document.body.innerText.includes('Настройка канала') && 
                                  document.body.innerText.includes('Я принимаю условия');
                return textFound;
            }""")
            
            if has_modal:
                log.info("Обнаружено окно 'Настройка канала'. Принимаем условия соглашения через JS...")
                
                # 1. Кликаем по чекбоксу
                await page.evaluate("""() => {
                    // Ищем стандартный input чекбокс
                    const inputCheckbox = document.querySelector('input[type="checkbox"]');
                    if (inputCheckbox) {
                        if (!inputCheckbox.checked) {
                            inputCheckbox.click();
                        }
                        return;
                    }
                    
                    // Ищем элемент с ролью checkbox
                    const roleCheckbox = document.querySelector('[role="checkbox"]');
                    if (roleCheckbox) {
                        if (roleCheckbox.getAttribute('aria-checked') !== 'true') {
                            roleCheckbox.click();
                        }
                        return;
                    }
                    
                    // Ищем по тексту "Я принимаю условия" и кликаем на родителя или контейнер
                    const elements = document.querySelectorAll('span, label, div, p');
                    for (const el of elements) {
                        if (el.innerText && el.innerText.includes('Я принимаю условия')) {
                            el.click();
                            const innerInp = el.querySelector('input');
                            if (innerInp) innerInp.click();
                            break;
                        }
                    }
                }""")
                await asyncio.sleep(1)
                
                # 2. Кликаем по кнопке "Продолжить"
                clicked_continue = await page.evaluate("""() => {
                    const allElements = document.querySelectorAll('button, div, span, a');
                    for (const el of allElements) {
                        if (el.innerText && el.innerText.trim() === 'Продолжить') {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                
                if clicked_continue:
                    log.info("Кнопка 'Продолжить' нажата успешно. Ждем сохранения настроек...")
                    await asyncio.sleep(4)
                    return True
                else:
                    log.warning("Кнопка 'Продолжить' не найдена через JS")
            return False
        except Exception as e:
            log.warning("Ошибка при обработке окна настройки канала: %s", str(e))
            return False

    async def _do_publish(
        self, page: Page, title: str, body: str, image_urls: list[str]
    ) -> dict:
        log.info("Переход в Студию: %s", ENTRY_URL)
        await page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)

        if await _is_login_page(page):
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

        # Заполняем редактор: заголовок → текст0 → картинка0 → текст1 → картинка1 → текст2
        await self._fill_editor_with_images(page, title, body, image_urls)
        await self._close_modals(page)

        # Фиксируем URL черновика — пригодится если публикация не пройдёт
        draft_url: Optional[str] = None
        if EDITOR_URL_RE.search(page.url):
            draft_url = page.url
            log.info("URL черновика: %s", draft_url)

        # Даём редактору стабилизироваться после изображений
        await self._stabilize_after_images(page)

        # Ждём активации кнопки публикации
        try:
            await self._wait_for_publish_ready(page)
        except PublishError as e:
            # Кнопка не активировалась — возвращаем черновик как страховку
            if not draft_url and EDITOR_URL_RE.search(page.url):
                draft_url = page.url
            log.error("Публикация не готова, черновик: %s", draft_url)
            return _err("publish_error", str(e), draft_url=draft_url)

        # Настраиваем перехват URL до кликов, чтобы не пропустить быстрый редирект
        intercepted_url: list[str] = []

        def _on_navigate(frame) -> None:
            if frame == page.main_frame and PUBLISHED_URL_RE.match(frame.url):
                intercepted_url.append(frame.url)

        page.on("framenavigated", _on_navigate)

        # Шаг 1: обычный клик по активной кнопке публикации.
        log.info("Нажимаем 'Опубликовать' (шаг 1)...")
        publish_button = page.locator(PUBLISH_BUTTON).first
        await publish_button.wait_for(state="visible", timeout=15_000)
        if await publish_button.is_disabled():
            raise PublishError(
                "Кнопка article-publish-btn осталась disabled после изображений"
            )
        await publish_button.scroll_into_view_if_needed()
        await publish_button.click()
        await asyncio.sleep(2)
        await _screenshot(page, "debug_after_publish1.png")

        # Обработка соглашения "Настройка канала" (для новых каналов)
        was_setup = await self._handle_channel_setup_modal(page)
        if was_setup:
            log.info("Повторно нажимаем 'Опубликовать' (шаг 1) после настройки канала...")
            await publish_button.wait_for(state="visible", timeout=15_000)
            if await publish_button.is_disabled():
                raise PublishError(
                    "Кнопка article-publish-btn заблокирована после настройки канала"
                )
            await publish_button.scroll_into_view_if_needed()
            await publish_button.click()
            await asyncio.sleep(2)
            await _screenshot(page, "debug_after_publish1_retry.png")

        # Шаг 2: капча если появилась, затем финальная кнопка
        await _handle_vk_captcha(page)

        try:
            await page.locator(SIDE_PUBLISH_BUTTON).first.wait_for(
                state="visible", timeout=10_000
            )
        except Exception:
            pass

        # Финальная кнопка: только реально активная.
        clicked2 = None
        side_publish_button = page.locator(SIDE_PUBLISH_BUTTON).first
        try:
            if await side_publish_button.count() > 0 and await side_publish_button.is_visible():
                if not await side_publish_button.is_disabled():
                    await side_publish_button.scroll_into_view_if_needed()
                    await side_publish_button.click()
                    clicked2 = "publish-btn"
        except Exception:
            pass

        if clicked2 is None:
            publish_buttons = page.locator('button:has-text("Опубликовать")')
            try:
                total_buttons = await publish_buttons.count()
                for _i in range(total_buttons):
                    candidate = publish_buttons.nth(_i)
                    if await candidate.is_visible() and not await candidate.is_disabled():
                        await candidate.scroll_into_view_if_needed()
                        await candidate.click()
                        clicked2 = f"btn[{_i}]"
                        break
            except Exception:
                pass

        if clicked2:
            log.info("Финальная кнопка нажата (%s)", clicked2)
        else:
            await _screenshot(page, "debug_no_publish_btn.png")
            log.warning("Финальная кнопка не найдена")



        # Проверяем CAPTCHA после клика
        await asyncio.sleep(1.5)
        if await _handle_vk_captcha(page):
            log.info("Ожидаем исчезновения капчи после клика (5с)...")
            await asyncio.sleep(5)
        if await _is_captcha_page(page):
            await _screenshot(page, "captcha_detected.png")
            raise CaptchaDetectedError(
                "SmartCaptcha при публикации — настройте мобильный прокси (PROXY_URL в .env)"
            )

        # Ждём URL опубликованной статьи
        try:
            await page.wait_for_url(PUBLISHED_URL_RE.pattern, timeout=20_000)
            published_url = page.url
        except Exception:
            if PUBLISHED_URL_RE.match(page.url):
                published_url = page.url
            elif intercepted_url:
                # URL был перехвачен до финального редиректа на editor/...
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

    async def _focus_last_paragraph(self, page: Page) -> None:
        """Надежно переводит фокус в последний пустой текстовый абзац, избегая атомных блоков картинок."""
        await page.evaluate(
            """() => {
                const paragraphs = document.querySelectorAll('.zen-editor-block-paragraph[data-block="true"]');
                if (paragraphs.length > 0) {
                    const last = paragraphs[paragraphs.length - 1];
                    last.click();
                    
                    const range = document.createRange();
                    const sel = window.getSelection();
                    range.selectNodeContents(last);
                    range.collapse(false);
                    sel.removeAllRanges();
                    sel.addRange(range);
                }
            }"""
        )
        await asyncio.sleep(0.5)

    async def _fill_editor_with_images(
        self, page: Page, title: str, body: str, image_urls: list[str]
    ) -> None:
        """Заполняет редактор Дзена заголовком и телом статьи.
        Тело статьи парсится последовательно. Маркеры [DZEN_IMAGE] или [IMAGE_N]
        заменяются на загрузку картинок из массива image_urls по порядку или по индексу.
        """
        log.info("Заполняем редактор (Draft.js) в гибком режиме...")
        textboxes = page.locator(TEXTBOX_SELECTOR)
        await asyncio.sleep(2)

        count = await textboxes.count()
        if count < 2:
            raise EditorNotFoundError(
                f"Найдено {count} полей ввода, ожидалось 2. URL: {page.url}"
            )

        # ── Заголовок ─────────────────────────────────────────────────────
        log.info("Заголовок: %s", title[:60])
        await textboxes.first.click(force=True)
        await self._paste_text(page, title)
        await asyncio.sleep(0.5)

        # ── Активируем поле тела ──────────────────────────────────────────
        await textboxes.nth(1).click(force=True)
        await asyncio.sleep(1)

        # Разбиваем тело статьи на текст и маркеры картинок
        # re.split с круглыми скобками сохраняет совпавшие разделители в списке
        blocks = re.split(r'(\[DZEN_IMAGE\]|\[IMAGE_\d+\])', body)
        image_index = 0

        for block in blocks:
            if not block:
                continue
            
            block_stripped = block.strip()
            # Проверяем, является ли блок маркером картинки
            is_image_marker = (block_stripped == "[DZEN_IMAGE]" or 
                               bool(re.match(r'^\[IMAGE_\d+\]$', block_stripped)))
            
            if is_image_marker:
                # Определяем индекс картинки из image_urls
                if block_stripped == "[DZEN_IMAGE]":
                    idx = image_index
                    image_index += 1
                else:
                    # Извлекаем число из [IMAGE_N]
                    idx = int(re.findall(r'\d+', block_stripped)[0])
                
                if idx < len(image_urls) and image_urls[idx]:
                    log.info("Вставляем картинку %d в позицию маркера %s: %s", idx, block_stripped, image_urls[idx][:60])
                    
                    # Создаем пустые строки для картинки
                    await page.keyboard.press('Enter')
                    await asyncio.sleep(0.2)
                    await page.keyboard.press('Enter')
                    await asyncio.sleep(0.2)
                    await page.keyboard.press('ArrowUp')
                    await asyncio.sleep(0.5)
                    
                    success = await _upload_image(page, image_urls[idx])
                    if success:
                        log.info("Картинка %d успешно загружена", idx)
                        await asyncio.sleep(2)
                        await self._focus_last_paragraph(page)
                        await _screenshot(page, f"debug_after_image_{idx}.png")
                    else:
                        log.warning("Не удалось загрузить картинку %d", idx)
                        # Возвращаем фокус в конец редактора
                        await textboxes.nth(1).click(force=True)
                        await asyncio.sleep(0.5)
                        await page.keyboard.press('Control+End')
                        await asyncio.sleep(0.5)
                else:
                    log.warning("Пропускаем маркер %s: нет картинки с индексом %d (всего картинок: %d)", 
                                block_stripped, idx, len(image_urls))
            else:
                # Обычный текст (HTML)
                log.info("Вставляем блок текста (%d символов)...", len(block))
                await self._paste_text(page, block)
                await asyncio.sleep(1)

    async def _stabilize_after_images(self, page: Page) -> None:
        """Ждёт, пока редактор успокоится после вставки изображений."""
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        await page.mouse.click(700, 400)
        await asyncio.sleep(0.5)
        await asyncio.sleep(10)

    async def _wait_for_publish_ready(self, page: Page) -> None:
        """Ждёт, пока кнопка публикации станет активной и сохранение завершится."""
        for _i in range(36):
            enabled = await page.evaluate(
                """() => {
                    const button = document.querySelector('[data-testid="article-publish-btn"]');
                    if (!button) return false;
                    const header = button.closest('header, [class*="header"], [class*="topbar"], [class*="EditorHeader"]');
                    const container = header || document.body;
                    const saving = container && container.innerText
                        ? container.innerText.includes('Идёт сохранение')
                        : false;
                    return !button.disabled && !saving;
                }"""
            )
            if enabled:
                log.info("Кнопка публикации готова через %dс", (_i + 1) * 5)
                return
            await asyncio.sleep(5)

        raise PublishError(
            "Кнопка article-publish-btn не активировалась после ожидания автосохранения"
        )

    async def _fill_editor(self, page: Page, title: str, body: str) -> None:
        log.info("Заполняем редактор (Draft.js)...")
        textboxes = page.locator(TEXTBOX_SELECTOR)
        await asyncio.sleep(2)

        count = await textboxes.count()
        if count < 2:
            raise EditorNotFoundError(
                f"Найдено {count} полей ввода, ожидалось 2. URL: {page.url}"
            )

        # Заголовок — первый textbox (вставка через буфер, мгновенно)
        await textboxes.first.click(force=True)
        await self._paste_text(page, title)
        await asyncio.sleep(0.5)

        # Тело — второй textbox (вставка через буфер, мгновенно)
        await textboxes.nth(1).click(force=True)
        await self._paste_text(page, body)
        await asyncio.sleep(0.5)

    async def _paste_text(self, page: Page, text: str) -> None:
        """Вставляет форматированный текст (если содержит HTML) или обычный текст."""
        is_html = "<" in text and ">" in text
        if is_html:
            log.info("Вставляем текст как HTML через буфер обмена...")
            success = await page.evaluate(
                """async (htmlContent) => {
                    try {
                        const blob = new Blob([htmlContent], { type: 'text/html' });
                        const item = new ClipboardItem({ 'text/html': blob });
                        await navigator.clipboard.write([item]);
                        return true;
                    } catch (e) {
                        console.error('Ошибка записи HTML в буфер:', e);
                        return false;
                    }
                }""",
                text
            )
            if success:
                await page.keyboard.press('Control+v')
                await asyncio.sleep(0.5)
                return
            else:
                log.warning("Не удалось записать HTML в буфер, вставляем как обычный текст...")
        
        await page.keyboard.insert_text(text)
        await asyncio.sleep(0.5)

    async def _close_modals(self, page: Page) -> None:
        """Закрывает все оверлеи и помогающие подсказки Дзена."""
        # Нажимаем Escape для закрытия простых диалогов
        await page.keyboard.press('Escape')
        await asyncio.sleep(0.5)
        
        await page.evaluate("""() => {
            const selectors = [
                '[data-testid*="close"]', '[data-testid*="cross"]',
                '[aria-label*="Закрыть"]',
                '[class*="close"]', '[class*="Close"]', '[class*="cross"]',
                '.editor--donations-promo-banner-popup__closeButton-1o',
                '[class*="onboarding-banner__close"]',
                '[data-testid="promo-editor-onboarding-button"]'
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
        # Закрываем modal-overlay через Escape если остался
        overlay = page.locator('[data-testid="modal-overlay"]')
        if await overlay.count() > 0:
            await page.keyboard.press('Escape')
            await asyncio.sleep(0.5)
        # Второй проход: клик по оверлею чтобы закрыть модалку
        if await overlay.count() > 0:
            try:
                await overlay.first.click(force=True)
                await asyncio.sleep(0.5)
            except Exception:
                pass
        await asyncio.sleep(0.5)

    async def _download_to_temp_file(
        self, url: str, prefix: str, extension: Optional[str] = None
    ) -> Optional[str]:
        try:
            temp_dir = tempfile.gettempdir()
            filename = os.path.basename(url).split("?")[0]
            if not extension:
                extension = os.path.splitext(filename)[1]
                if not extension or len(extension) > 5:
                    extension = ".jpg"

            temp_filename = f"dzen_{prefix}_{os.urandom(4).hex()}{extension}"
            filepath = os.path.join(temp_dir, temp_filename)

            log.info("Скачиваем файл во временный файл: %s", filepath)
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(url, headers={"User-Agent": USER_AGENT})
                resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(resp.content)
            log.info("Файл успешно скачан. Размер: %d байт", os.path.getsize(filepath))
            return filepath
        except Exception as e:
            log.warning("Ошибка скачивания файла %s: %s", url, e)
            return None

    async def _do_publish_post(
        self, page: Page, text: str, image_urls: list[str]
    ) -> dict:
        log.info("Переход в Студию для публикации поста: %s", ENTRY_URL)
        await page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)

        if await _is_login_page(page):
            raise SessionExpiredError("Сессия истекла — редирект на страницу входа")

        if await _is_captcha_page(page):
            raise CaptchaDetectedError("SmartCaptcha обнаружена")

        await self._close_modals(page)

        log.info("Нажимаем кнопку создания публикации...")
        await page.wait_for_selector(ADD_BUTTON, timeout=15_000)
        await page.evaluate(
            "document.querySelector('[data-testid=\"add-publication-button\"]').click()"
        )
        await asyncio.sleep(2)

        log.info("Выбираем 'Написать пост'...")
        await page.evaluate(
            """() => {
                const all = document.querySelectorAll('span, button, li, [role="menuitem"]');
                for (const el of all) {
                    const txt = el.innerText ? el.innerText.trim() : "";
                    if (txt === 'Написать пост' || txt === 'Создать пост' || txt === 'Пост') {
                        el.click();
                        return;
                    }
                }
            }"""
        )
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(5)
        await self._close_modals(page)

        log.info("Ожидаем появления поля ввода поста...")
        post_input = None
        for attempt in range(6):
            await self._close_modals(page)
            for sel in ['.ql-editor', '[contenteditable="true"]', '[role="textbox"]']:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    post_input = loc
                    break
            if post_input:
                break
            log.info("Поле ввода не найдено, попытка %d/6, ждем 2с...", attempt + 1)
            await asyncio.sleep(2)

        if not post_input:
            raise EditorNotFoundError(
                f"Поле ввода поста не найдено. URL: {page.url}"
            )

        log.info("Вставляем текст поста...")
        await post_input.click(force=True)
        await self._paste_text(page, text)
        await asyncio.sleep(1)

        # Загрузка изображений для поста
        if image_urls:
            log.info("Загрузка %d изображений для поста...", len(image_urls))
            files_to_upload = []
            temp_files = []
            try:
                for idx, img_url in enumerate(image_urls[:10]):
                    if img_url.startswith(("http://", "https://")):
                        temp_filepath = await self._download_to_temp_file(img_url, f"post_img_{idx}")
                        if temp_filepath:
                            temp_files.append(temp_filepath)
                            files_to_upload.append(temp_filepath)
                    else:
                        if os.path.exists(img_url):
                            files_to_upload.append(img_url)
                            log.info("Используем локальное изображение на VPS: %s", img_url)
                        else:
                            log.warning("Локальное изображение не найдено на VPS: %s", img_url)

                if files_to_upload:
                    file_input = page.locator('input[type="file"]').first
                    if await file_input.count() > 0:
                        log.info("Передаем файлы в input[type=file]...")
                        await file_input.set_input_files(files_to_upload)
                        wait_time = len(files_to_upload) * 6
                        log.info("Ожидаем завершения загрузки картинок (%dс)...", wait_time)
                        await asyncio.sleep(wait_time)
                    else:
                        log.warning("input[type=file] не найден в редакторе поста")
            finally:
                for path in temp_files:
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass

        await page.keyboard.press("Escape")
        await asyncio.sleep(2)

        # Публикация
        log.info("Ищем кнопку публикации поста...")
        
        intercepted_urls = []
        async def _on_response(response):
            try:
                if response.status == 200 and "application/json" in response.headers.get("content-type", ""):
                    text = await response.text()
                    matches = re.findall(r"https://dzen\.ru/[abv]/[A-Za-z0-9_-]+", text)
                    if matches:
                        intercepted_urls.extend(matches)
            except Exception:
                pass

        page.on("response", _on_response)

        publish_btn = None
        for sel in [
            '[data-testid="post-publish-btn"]',
            '.brief-editor--brief-desktop-editor-content__publishButton-1U',
            '[data-testid="publish-btn"]',
            '[data-testid="article-publish-btn"]',
            'button:has-text("Опубликовать")',
            'button:has-text("Поделиться")',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible() and not await loc.is_disabled():
                publish_btn = loc
                break

        if not publish_btn:
            log.info("Пробуем найти и кликнуть кнопку через JS...")
            success = await page.evaluate(
                """() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const target = btns.find(b => {
                        const txt = b.innerText ? b.innerText.trim() : "";
                        return (txt.includes("Опубликовать") || txt.includes("Поделиться")) && !b.disabled;
                    });
                    if (target) {
                        target.click();
                        return true;
                    }
                    return false;
                }"""
            )
            if not success:
                await _screenshot(page, "debug_post_publish_btn_not_found.png")
                raise PublishError("Кнопка публикации поста не найдена или неактивна")
        else:
            await publish_btn.scroll_into_view_if_needed()
            await publish_btn.click()

        log.info("Кнопка публикации нажата. Ожидаем редиректа или подтверждения...")

        # Ждём редирект на URL поста (dzen.ru/b/XXX или dzen.ru/a/XXX)
        POST_URL_RE = re.compile(r"https://dzen\.ru/[abv]/[A-Za-z0-9_-]+")
        try:
            await page.wait_for_url(
                lambda url: bool(POST_URL_RE.match(url)),
                timeout=15_000,
            )
            published_url = page.url
            log.info("Редирект на URL поста: %s", published_url)
        except Exception:
            # Редиректа не было — проверяем перехваченные URL из API
            await asyncio.sleep(3)
            if intercepted_urls:
                published_url = intercepted_urls[-1]
                log.info("URL поста из перехваченных ответов: %s", published_url)
            else:
                published_url = page.url
                log.warning("URL поста не получен после первого клика. Текущий: %s", published_url)

        await _handle_vk_captcha(page)

        if await _is_captcha_page(page):
            await _screenshot(page, "captcha_detected.png")
            raise CaptchaDetectedError("SmartCaptcha обнаружена при публикации поста")

        # --- RETRY: если остались на editor — пост попал в черновик ---
        # Пробуем найти и нажать кнопку "Опубликовать" ещё раз
        if "profile/editor" in page.url or "briefEditorPublicationId" in page.url:
            log.warning("Остались на странице редактора после клика. Повторная попытка публикации...")
            await _screenshot(page, "debug_post_still_draft.png")
            await asyncio.sleep(2)

            retried = False
            for retry_sel in [
                '[data-testid="post-publish-btn"]',
                '[data-testid="publish-btn"]',
                '[data-testid="article-publish-btn"]',
                'button:has-text("Опубликовать")',
                'button:has-text("Поделиться")',
            ]:
                loc = page.locator(retry_sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    try:
                        if not await loc.is_disabled():
                            await loc.scroll_into_view_if_needed()
                            await loc.click()
                            retried = True
                            log.info("Retry: нажата кнопка публикации (%s)", retry_sel)
                            break
                    except Exception:
                        pass

            if not retried:
                # JS-клик как последний шанс
                retried = await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const target = btns.find(b => {
                        const txt = b.innerText ? b.innerText.trim() : "";
                        return (txt.includes("Опубликовать") || txt.includes("Поделиться")) && !b.disabled;
                    });
                    if (target) { target.click(); return true; }
                    return false;
                }""")
                if retried:
                    log.info("Retry: кнопка нажата через JS")

            if retried:
                # Ждём редирект после retry
                try:
                    await page.wait_for_url(
                        lambda url: bool(POST_URL_RE.match(url)),
                        timeout=20_000,
                    )
                    published_url = page.url
                    log.info("Retry успешен! URL поста: %s", published_url)
                except Exception:
                    await asyncio.sleep(5)
                    if intercepted_urls:
                        published_url = intercepted_urls[-1]
                        log.info("Retry: URL из перехваченных: %s", published_url)
                    else:
                        published_url = page.url
                        log.warning("Retry: URL поста всё ещё не получен: %s", published_url)

        # Финальная проверка: пробуем найти ссылку на пост на странице
        if not POST_URL_RE.match(published_url):
            try:
                href = await page.evaluate("""() => {
                    const links = Array.from(document.querySelectorAll('a'));
                    for (const a of links) {
                        if (/dzen\\.ru\\/[abv]\\//.test(a.href)) return a.href;
                    }
                    return null;
                }""")
                if href:
                    published_url = href
                    log.info("URL поста найден на странице: %s", published_url)
            except Exception:
                pass

        await _screenshot(page, "debug_post_final.png")
        log.info("Пост опубликован. URL: %s", published_url)
        return {"success": True, "published_url": published_url}

    async def _do_publish_video(
        self, page: Page, video_url: str, title: str, description: str, cover_url: Optional[str] = None
    ) -> dict:
        log.info("Переход в Студию для публикации видео: %s", ENTRY_URL)
        await page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)

        if await _is_login_page(page):
            raise SessionExpiredError("Сессия истекла — редирект на страницу входа")

        if await _is_captcha_page(page):
            raise CaptchaDetectedError("SmartCaptcha обнаружена")

        await self._close_modals(page)

        log.info("Нажимаем кнопку создания публикации...")
        await page.wait_for_selector(ADD_BUTTON, timeout=15_000)
        await page.evaluate(
            "document.querySelector('[data-testid=\"add-publication-button\"]').click()"
        )
        await asyncio.sleep(2)

        log.info("Выбираем 'Загрузить видео'...")
        await page.evaluate(
            """() => {
                const all = document.querySelectorAll('span, button, li, [role="menuitem"]');
                for (const el of all) {
                    const txt = el.innerText ? el.innerText.trim() : "";
                    if (txt === 'Загрузить видео' || txt === 'Видео' || txt === 'Загрузить ролик' || txt === 'Загрузить видеоролик') {
                        el.click();
                        return;
                    }
                }
            }"""
        )
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(5)

        temp_video_path = None
        video_file_to_upload = ""
        if video_url.startswith(("http://", "https://")):
            temp_video_path = await self._download_to_temp_file(video_url, "video", extension=".mp4")
            if not temp_video_path:
                raise PublishError("Не удалось скачать видеофайл")
            video_file_to_upload = temp_video_path
        else:
            if not os.path.exists(video_url):
                raise PublishError(f"Локальный видеофайл не найден на VPS: {video_url}")
            video_file_to_upload = video_url
            log.info("Используем локальный видеофайл на VPS: %s", video_file_to_upload)

        temp_cover_path = None
        cover_file_to_upload = None
        if cover_url:
            if cover_url.startswith(("http://", "https://")):
                temp_cover_path = await self._download_to_temp_file(cover_url, "video_cover")
                cover_file_to_upload = temp_cover_path
            else:
                if os.path.exists(cover_url):
                    cover_file_to_upload = cover_url
                    log.info("Используем локальную обложку на VPS: %s", cover_file_to_upload)

        try:
            log.info("Ожидаем появления инпута для загрузки видео...")
            file_input = None
            for attempt in range(6):
                loc = page.locator('input[type="file"]').first
                if await loc.count() > 0:
                    file_input = loc
                    break
                log.info("Инпут для загрузки видео не найден, попытка %d/6, ждем 2с...", attempt + 1)
                await asyncio.sleep(2)

            if not file_input:
                raise EditorNotFoundError("Инпут для загрузки видеофайла не найден")

            log.info("Загружаем видеофайл через set_input_files...")
            await file_input.set_input_files(video_file_to_upload)

            log.info("Ожидаем появления формы метаданных (до 30с)...")
            title_input = None
            for attempt in range(30):
                for sel in [
                    'textarea[placeholder*="назван"]',
                    'textarea[placeholder*="обязательно"]',
                    'textarea[placeholder*="Название"]',
                    'input[placeholder*="Название"]',
                    'input[placeholder*="название"]',
                    '[role="textbox"]',
                ]:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        title_input = loc
                        break
                if title_input:
                    log.info("Форма метаданных появилась через %dс", attempt)
                    break
                await asyncio.sleep(1)

            if not title_input:
                log.warning("Форма метаданных (поле названия) не появилась за 30с")

            if title_input:
                await title_input.click(force=True)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
                await self._paste_text(page, title)
                log.info("Название видео введено: %s", title[:40])
            else:
                log.warning("Поле названия видео не найдено")

            # Описание видео
            desc_input = None
            for sel in [
                '.ql-editor',
                '[contenteditable="true"]',
                'textarea[placeholder*="Описание"]',
                'textarea[placeholder*="описание"]',
            ]:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    desc_input = loc
                    break

            if desc_input:
                await desc_input.click(force=True)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
                await self._paste_text(page, description)
                log.info("Описание видео введено: %s", description[:40])
            else:
                log.warning("Поле описания видео не найдено")

            # Обложка видео
            if cover_file_to_upload:
                cover_input = None
                file_inputs = page.locator('input[type="file"]')
                count_inputs = await file_inputs.count()
                if count_inputs > 1:
                    cover_input = file_inputs.nth(1)
                elif count_inputs == 1:
                    cover_input = file_inputs.first

                if cover_input:
                    log.info("Загружаем обложку для видео...")
                    await cover_input.set_input_files(cover_file_to_upload)
                    await asyncio.sleep(3)
                else:
                    log.warning("Инпут для загрузки обложки не найден")

            # Ожидание окончания загрузки
            log.info("Ожидаем завершения загрузки видео на server (до 180с)...")
            
            intercepted_urls = []
            async def _on_response(response):
                try:
                    if response.status == 200 and "application/json" in response.headers.get("content-type", ""):
                        text = await response.text()
                        matches = re.findall(r"https://dzen\.ru/[abv]/[A-Za-z0-9_-]+", text)
                        if matches:
                            intercepted_urls.extend(matches)
                except Exception:
                    pass

            page.on("response", _on_response)

            publish_btn = None
            for attempt in range(36):
                for sel in [
                    '[data-testid="video-publish-btn"]',
                    '[data-testid="publish-btn"]',
                    'button:has-text("Опубликовать")',
                ]:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible() and not await loc.is_disabled():
                        publish_btn = loc
                        break
                if publish_btn:
                    log.info("Видео загружено, кнопка публикации готова через %dс", attempt * 5)
                    break
                await asyncio.sleep(5)

            if not publish_btn:
                log.info("Пробуем принудительно нажать через JS...")
                success = await page.evaluate(
                    """() => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const target = btns.find(b => b.innerText && b.innerText.includes("Опубликовать") && !b.disabled);
                        if (target) {
                            target.click();
                            return true;
                        }
                        return false;
                    }"""
                )
                if not success:
                    await _screenshot(page, "debug_video_publish_btn_not_found.png")
                    raise PublishError("Видео не загрузилось вовремя или кнопка публикации заблокирована")
            else:
                await publish_btn.scroll_into_view_if_needed()
                await publish_btn.click()

            log.info("Кнопка публикации нажата. Ожидаем завершения...")
            await asyncio.sleep(3)
            await _handle_vk_captcha(page)
            await asyncio.sleep(3)

            if await _is_captcha_page(page):
                await _screenshot(page, "captcha_detected.png")
                raise CaptchaDetectedError("SmartCaptcha обнаружена при публикации видео")

            published_url = page.url
            if intercepted_urls:
                published_url = intercepted_urls[-1]
            elif "publications" in page.url or "profile/editor" in page.url:
                log.info("Ищем URL видео на странице публикаций...")
                try:
                    href = await page.evaluate("""() => {
                        const links = Array.from(document.querySelectorAll('a'));
                        for (const a of links) {
                            if (a.href.includes('dzen.ru/b/') || a.href.includes('dzen.ru/v/') || a.href.includes('dzen.ru/a/')) {
                                return a.href;
                            }
                        }
                        return null;
                    }""")
                    if href:
                        published_url = href
                except Exception:
                    pass

            log.info("Видео успешно опубликовано. URL: %s", published_url)
            return {"success": True, "published_url": published_url}

        finally:
            if temp_video_path and os.path.exists(temp_video_path):
                try:
                    os.remove(temp_video_path)
                except Exception:
                    pass
            if temp_cover_path and os.path.exists(temp_cover_path):
                try:
                    os.remove(temp_cover_path)
                except Exception:
                    pass

    async def _do_publish_reel(
        self, page: Page, video_url: str, description: str
    ) -> dict:
        """Публикация РОЛИКА (вертикальное короткое видео).

        Отличия от обычного видео:
          - нет отдельного заголовка (описание = «шапка», ≤200 символов);
          - обложка не нужна (берётся кадр из ролика);
          - отдельный пункт меню «Ролик» / «Загрузить ролик».

        ВНИМАНИЕ: селекторы пункта меню и поля описания ролика
        нужно сверить с живым UI Студии (см. DZEN.md). Ниже — набор
        кандидатов; при смене интерфейса обновить список.
        """
        log.info("Переход в Студию для публикации ролика: %s", ENTRY_URL)
        await page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)

        if await _is_login_page(page):
            raise SessionExpiredError("Сессия истекла — редирект на страницу входа")
        if await _is_captcha_page(page):
            raise CaptchaDetectedError("SmartCaptcha обнаружена")

        await self._close_modals(page)

        log.info("Нажимаем кнопку создания публикации...")
        await page.wait_for_selector(ADD_BUTTON, timeout=15_000)
        await page.evaluate(
            "document.querySelector('[data-testid=\"add-publication-button\"]').click()"
        )
        await asyncio.sleep(2)

        log.info("Выбираем пункт меню 'Ролик'...")
        await page.evaluate(
            """() => {
                const all = document.querySelectorAll('span, button, li, [role="menuitem"]');
                for (const el of all) {
                    const txt = el.innerText ? el.innerText.trim() : "";
                    if (txt === 'Загрузить ролик' || txt === 'Ролик'
                        || txt === 'Загрузить видеоролик' || txt === 'Снять ролик'
                        || txt === 'Добавить ролик') {
                        el.click();
                        return;
                    }
                }
            }"""
        )
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(5)

        # Готовим файл ролика
        temp_video_path = None
        if video_url.startswith(("http://", "https://")):
            temp_video_path = await self._download_to_temp_file(video_url, "reel", extension=".mp4")
            if not temp_video_path:
                raise PublishError("Не удалось скачать файл ролика")
            video_file = temp_video_path
        else:
            if not os.path.exists(video_url):
                raise PublishError(f"Локальный файл ролика не найден на VPS: {video_url}")
            video_file = video_url
            log.info("Используем локальный файл ролика на VPS: %s", video_file)

        try:
            log.info("Ожидаем появления инпута для загрузки ролика...")
            file_input = None
            for attempt in range(6):
                loc = page.locator('input[type="file"]').first
                if await loc.count() > 0:
                    file_input = loc
                    break
                log.info("Инпут ролика не найден, попытка %d/6, ждём 2с...", attempt + 1)
                await asyncio.sleep(2)

            if not file_input:
                raise EditorNotFoundError("Инпут для загрузки файла ролика не найден")

            log.info("Загружаем файл ролика через set_input_files...")
            await file_input.set_input_files(video_file)

            # Поле описания (заголовка у ролика нет)
            log.info("Ожидаем форму описания ролика (до 30с)...")
            desc_input = None
            for attempt in range(30):
                for sel in [
                    '.ql-editor',
                    '[contenteditable="true"]',
                    'textarea[placeholder*="писани"]',
                    'textarea[placeholder*="Добавьте описание"]',
                    'textarea[placeholder*="Опишите"]',
                    '[role="textbox"]',
                ]:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        desc_input = loc
                        break
                if desc_input:
                    log.info("Форма описания ролика появилась через %dс", attempt)
                    break
                await asyncio.sleep(1)

            if desc_input:
                await desc_input.click(force=True)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
                await self._paste_text(page, description)
                log.info("Описание ролика введено (%d симв.): %s", len(description), description[:40])
            else:
                log.warning("Поле описания ролика не найдено")

            # Ждём завершения загрузки + жмём публикацию
            log.info("Ожидаем завершения загрузки ролика (до 180с)...")
            intercepted_urls: list[str] = []

            async def _on_response(response):
                try:
                    if response.status == 200 and "application/json" in response.headers.get("content-type", ""):
                        t = await response.text()
                        m = re.findall(r"https://dzen\.ru/[abv]/[A-Za-z0-9_-]+", t)
                        if m:
                            intercepted_urls.extend(m)
                except Exception:
                    pass

            page.on("response", _on_response)

            publish_btn = None
            for attempt in range(36):
                for sel in [
                    '[data-testid="video-publish-btn"]',
                    '[data-testid="publish-btn"]',
                    'button:has-text("Опубликовать")',
                ]:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible() and not await loc.is_disabled():
                        publish_btn = loc
                        break
                if publish_btn:
                    log.info("Ролик загружен, кнопка публикации готова через %dс", attempt * 5)
                    break
                await asyncio.sleep(5)

            if not publish_btn:
                log.info("Пробуем принудительно нажать 'Опубликовать' через JS...")
                success = await page.evaluate(
                    """() => {
                        const b = Array.from(document.querySelectorAll('button'))
                            .find(x => x.innerText && x.innerText.includes('Опубликовать') && !x.disabled);
                        if (b) { b.click(); return true; }
                        return false;
                    }"""
                )
                if not success:
                    await _screenshot(page, "debug_reel_publish_btn_not_found.png")
                    raise PublishError("Ролик не загрузился вовремя или кнопка публикации заблокирована")
            else:
                await publish_btn.scroll_into_view_if_needed()
                await publish_btn.click()

            log.info("Кнопка публикации ролика нажата. Ожидаем завершения...")
            await asyncio.sleep(3)
            await _handle_vk_captcha(page)
            await asyncio.sleep(3)

            if await _is_captcha_page(page):
                await _screenshot(page, "captcha_detected.png")
                raise CaptchaDetectedError("SmartCaptcha обнаружена при публикации ролика")

            published_url = intercepted_urls[-1] if intercepted_urls else page.url
            log.info("Ролик успешно опубликован. URL: %s", published_url)
            return {"success": True, "published_url": published_url}

        finally:
            if temp_video_path and os.path.exists(temp_video_path):
                try:
                    os.remove(temp_video_path)
                except Exception:
                    pass



# ── Helpers ───────────────────────────────────────────────────────────────────────────────

def _err(code: str, message: str, draft_url: Optional[str] = None) -> dict:
    result: dict = {"success": False, "error": code, "message": message}
    if draft_url:
        result["draft_url"] = draft_url
    return result


async def _handle_vk_captcha(page: Page) -> bool:
    """Обнаруживает и кликает VK/Yandex SmartCaptcha ('Я не робот') во фреймах."""
    try:
        # 1. Попытка кликнуть в iframe (SmartCaptcha)
        for frame in page.frames:
            if "captcha" in frame.url or "smart" in frame.url:
                log.info("Найдена SmartCaptcha во фрейме: %s", frame.url[:80])
                selectors = [
                    'label.vkc__Checkbox-module__Checkbox',
                    '.vkc__Checkbox-module__Checkbox',
                    '.vkuiInternalTappable',
                    '.smart-captcha__checkbox',
                    '.CheckboxCaptcha-Anchor',
                    '.CheckboxCaptcha-Button',
                    '.checkbox__input',
                    'input[type="checkbox"]',
                    '.smart-captcha',
                    '.smart-captcha__label',
                ]
                for sel in selectors:
                    locator = frame.locator(sel).first
                    if await locator.count() > 0:
                        log.info("Кликаем чекбокс SmartCaptcha по селектору %s внутри фрейма", sel)
                        try:
                            # Сначала пробуем JS-клик на label или инпуте
                            await locator.evaluate("el => el.click()")
                            log.info("JS-клик по %s во фрейме выполнен успешно", sel)
                        except Exception as je:
                            log.warning("JS-клик во фрейме не удался: %s", je)
                        
                        try:
                            # Для надежности дублируем forced кликом
                            await locator.click(force=True, timeout=2000)
                            log.info("Forced-клик Playwright по %s выполнен", sel)
                        except Exception as ce:
                            log.warning("Forced-клик не удался: %s", ce)
                        
                        await asyncio.sleep(5)
                        return True

        # 2. Обычная попытка на основной странице (если без iframe)
        found = await page.evaluate("""
            () => {
                const selectors = [
                    '#not-robot-captcha-checkbox',
                    '[aria-label="Я не\\u00a0робот"]',
                    '[aria-label="Я не робот"]',
                    '.CheckboxCaptcha-Anchor',
                    'input[type="checkbox"][id*="captcha"]'
                ];
                for (const sel of selectors) {
                    const cb = document.querySelector(sel);
                    if (cb) {
                        const label = cb.closest('label') || cb.parentElement;
                        if (label) label.click();
                        else cb.click();
                        return true;
                    }
                }
                for (const el of document.querySelectorAll('label, span, div')) {
                    if (el.innerText && el.innerText.trim() === 'Я не робот') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        if found:
            log.info("VK captcha 'Я не робот' на основной странице — кликнули чекбокс, ждём 3с...")
            await asyncio.sleep(3)
        return found
    except Exception as e:
        log.warning("Ошибка обработки капчи: %s", e)
        return False


async def _is_captcha_page(page: Page) -> bool:
    if "smartcaptcha" in page.url:
        return True
    try:
        indicators = [
            '[class*="captcha"]',
            '[id*="captcha"]',
            'iframe[src*="captcha"]',
            'iframe[src*="smartcaptcha"]',
        ]
        for sel in indicators:
            if await page.locator(sel).first.count() > 0:
                return True
        # Текстовый признак чекбокс-капчи (появляется при публикации)
        if await page.locator('text="Подтвердите, что вы не робот"').count() > 0:
            return True
    except Exception:
        pass
    return False


async def _upload_image(page: Page, image_url: str) -> bool:
    """Загружает изображение по URL в редактор Дзена через sideButton.

    Flow:
    1. Скачиваем изображение во временный файл на диске VPS.
    2. Закрываем любой открытый диалог в Дзене (Escape).
    3. Ждём появления sideButton (кнопка + слева от пустой строки).
    4. Кликаем sideButton.
    5. Загружаем локальный файл через input[type="file"].
    6. Если не удалось, пробуем fallback-вставку URL.
    """
    temp_filepath = ""
    file_to_upload = ""
    try:
        if image_url.startswith(("http://", "https://")):
            # 1. Скачиваем изображение во временный файл
            temp_dir = tempfile.gettempdir()
            temp_filename = os.path.basename(image_url).split('?')[0]
            # Гарантируем корректное расширение для Дзена
            if not any(temp_filename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp']):
                temp_filename += ".jpg"
            temp_filepath = os.path.join(temp_dir, f"dzen_upload_{temp_filename}")
            
            log.info("Скачиваем изображение во временный файл: %s", temp_filepath)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                with open(temp_filepath, "wb") as f:
                    f.write(resp.content)
            log.info("Изображение успешно скачано. Размер: %d байт", os.path.getsize(temp_filepath))
            file_to_upload = temp_filepath
        else:
            # Это локальный файл на VPS
            if not os.path.exists(image_url):
                log.warning("Локальный файл не найден на VPS: %s", image_url)
                return False
            file_to_upload = image_url
            log.info("Используем локальный файл на VPS: %s", file_to_upload)

        # Закрываем любой открытый диалог
        await page.keyboard.press('Escape')
        await asyncio.sleep(0.5)

        # Ищем sideButton — повторяем до 5 раз по 1с
        has_btn = False
        for attempt in range(5):
            has_btn = await page.evaluate(
                """() => !!document.querySelector('button[class*="sideButton"]') || 
                           !!document.querySelector('button[data-tip="Вставить изображение"]')"""
            )
            if has_btn:
                log.info("sideButton найдена (попытка %d)", attempt + 1)
                break
            log.debug("sideButton не найдена, попытка %d/5, ждём 1с...", attempt + 1)
            await asyncio.sleep(1)

        if not has_btn:
            # Диагностика: логируем все кнопки
            all_btns = await page.query_selector_all('button')
            btn_info = []
            for b in all_btns[:30]:
                tid = await b.get_attribute('data-testid') or ''
                aria = await b.get_attribute('aria-label') or ''
                cls = (await b.get_attribute('class') or '')[:60]
                txt = (await b.inner_text())[:30]
                if tid or aria or txt:
                    btn_info.append(f"testid={tid!r} aria={aria!r} text={txt!r} class={cls!r}")
            log.warning("sideButton не найдена после 5 попыток. Кнопки: %s", '; '.join(btn_info))
            return False

        # Кликаем sideButton через JS (надёжнее чем Playwright click при оверлеях)
        await page.evaluate(
            """() => {
                const b = document.querySelector('button[class*="sideButton"]') ||
                          document.querySelector('button[data-tip="Вставить изображение"]');
                if(b) b.click();
            }"""
        )
        await asyncio.sleep(1.5)  # ждём открытия диалога URL или появления input[type=file]

        # Пытаемся найти input[type="file"]
        file_input = page.locator('input[type="file"]').first
        if await file_input.count() > 0:
            log.info("Загружаем локальный файл через set_input_files...")
            await file_input.set_input_files(file_to_upload)
            log.info("Файл передан. Ждем 12 секунд завершения загрузки...")
            await asyncio.sleep(12)
            log.info("Изображение вставлено как файл")
            return True
        
        log.warning("input[type=file] не найден, пробуем fallback с URL вставкой...")

        # Ищем поле для ввода URL (fallback)
        url_input = page.locator(
            'input[placeholder="Ссылка"], input[placeholder*="Ссылк"], '
            'input[placeholder*="URL"], input[placeholder*="url"], input[placeholder*="http"]'
        ).first

        if await url_input.count() == 0:
            log.warning("Поле URL не найдено в диалоге изображения")
            await page.keyboard.press('Escape')
            return False

        # Копируем URL в буфер обмена браузера
        await page.evaluate(
            "async (text) => { await navigator.clipboard.writeText(text); }",
            image_url
        )
        await asyncio.sleep(0.5)

        # Вставляем URL через Control+v
        await url_input.click()
        await asyncio.sleep(0.2)
        await page.keyboard.press('Control+v')
        log.info("URL вставлен через Control+v, ждём закрытия поп-апа (3с)...")
        await asyncio.sleep(3)

        # Ждём загрузки изображения
        log.info("Ждём загрузки изображения (10с)...")
        await asyncio.sleep(10)

        # Если по какой-то причине диалог остался открытым, нажимаем Escape
        if await url_input.count() > 0 and await url_input.is_visible():
            log.info("Диалог ввода URL остался открыт, нажимаем Escape...")
            await page.keyboard.press('Escape')
            await asyncio.sleep(2)

        log.info("Изображение вставлено через URL")
        return True

    except Exception as e:
        log.warning("Ошибка загрузки изображения %s: %s", image_url, e)
        await page.keyboard.press('Escape')
        return False
    finally:
        # Очищаем временный файл
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                log.info("Временный файл удален: %s", temp_filepath)
            except Exception as e:
                log.warning("Не удалось удалить временный файл %s: %s", temp_filepath, e)


async def _screenshot(page: Page, filename: str) -> None:
    try:
        await page.screenshot(path=filename)
    except Exception:
        pass
