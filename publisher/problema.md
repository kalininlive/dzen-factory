# Проблема: публикация статьи в Яндекс Дзен с изображениями

## Что установлено

**VPS**: `ymsmmxpruz` (тот же сервер что и n8n)  
**Сервис**: `/opt/dzen-publisher/` — Python FastAPI + Patchright (антидетект Playwright)  
**Порт**: `8001`  
**Systemd**: `dzen-publisher.service`  
**Основной файл**: `/opt/dzen-publisher/publisher/dzen.py`  

**Стек**:
- Python 3.x + FastAPI (uvicorn)
- Patchright (антидетект обёртка над Playwright/Chromium)
- Headless Chromium на VPS
- Cookies Дзена в `/opt/dzen-publisher/publisher/cookies/`

**n8n workflow**: https://n8n.kalininlive.ru/workflow/zYMvxDZyqAl0yNdp  
Workflow генерирует статью (текст + 3 изображения по URL) и отправляет POST на `http://localhost:8001/publish`

---

## Как работает публикация

1. n8n отправляет POST `/publish` с JSON:
```json
{
  "title": "Заголовок статьи",
  "body": "[IMAGE_0]\nтекст\n[IMAGE_1]\nтекст\n[IMAGE_2]\nтекст",
  "image_urls": ["https://...", "https://...", "https://..."]
}
```

2. `dzen.py` запускает Chromium, открывает `dzen.ru/profile/editor/create`
3. Заполняет редактор (Draft.js):
   - Вставляет заголовок в первый textbox
   - Для каждого изображения: нажимает Enter, кликает sideButton (кнопка вставки изображения слева от курсора), вставляет URL в диалоге, ждёт загрузки (15с), закрывает диалог (Escape)
   - После каждого изображения вставляет текст через ClipboardEvent
4. Нажимает `article-publish-btn` (кнопка "Опубликовать" в хедере)
5. Ждёт всплывающего попапа "Публикация" с кнопкой `publish-btn`
6. Кликает `publish-btn`
7. Обрабатывает VK капчу "Я не робот" если появляется
8. Ждёт редиректа на URL вида `dzen.ru/a/...`

---

## Что работает ✅

- Заголовок вставляется
- Все 3 изображения вставляются через URL диалог (картинка 0, 1, 2)
- Текст после каждого изображения вставляется
- Без изображений (текст-only) — публикация работает полностью

---

## Что НЕ работает ❌

### Проблема 1 (ОСНОВНАЯ): кнопка `article-publish-btn` остаётся `disabled` после вставки изображений

После вставки 3 изображений + текст, Дзен начинает автосохранение ("Идёт сохранение" в статусе).  
**Кнопка `article-publish-btn` остаётся `disabled=True` бесконечно** — даже через 90+ секунд.  
Пока кнопка disabled:
- JS `.click()` через `page.evaluate()` — React игнорирует клик на disabled кнопке
- Playwright `.click(force=True)` — открывает НЕПРАВИЛЬНУЮ боковую панель настроек (не попап публикации)
- Попап "Публикация" с кнопкой `publish-btn` никогда не открывается

**Без изображений**: кнопка enabled сразу, JS клик работает, попап открывается, публикация успешна.

**Один раз** попап всё-таки открылся (скриншот `after_publish.png` в папке `publisher/`). В нём появилась VK капча "Подтвердите, что вы не робот" с чекбоксом "Я не робот". Кнопка "Опубликовать" в попапе была серая (заблокирована капчей).

### Проблема 2 (вытекает из 1): fallback кликает неправильную кнопку

Код ищет `publish-btn` (не находит — попап не открылся), затем ищет все кнопки с текстом "Опубликовать":
- Находит только 1 штуку — тот же самый `article-publish-btn` (disabled)
- Кликает `btn[0]` — это снова `article-publish-btn`
- URL статьи не получен → ошибка

---

## Последний лог (полный)

```
Jun 02 14:30:16 ymsmmxpruz systemd[1]: Stopped Dzen Publisher Service.
Jun 02 14:30:16 ymsmmxpruz systemd[1]: dzen-publisher.service: Consumed 34.994s CPU time.
Jun 02 14:30:16 ymsmmxpruz systemd[1]: Started Dzen Publisher Service.
Jun 02 14:30:17 ymsmmxpruz uvicorn[383815]: INFO:     Started server process [383815]
Jun 02 14:30:17 ymsmmxpruz uvicorn[383815]: INFO:     Waiting for application startup.
Jun 02 14:30:17 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:30:17,434 INFO Publisher Service запускается (порт 8001)
Jun 02 14:30:17 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:30:17,434 INFO Cookies: найдены
Jun 02 14:30:17 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:30:17,435 INFO Прокси: настроен
Jun 02 14:30:17 ymsmmxpruz uvicorn[383815]: INFO:     Application startup complete.
Jun 02 14:30:17 ymsmmxpruz uvicorn[383815]: INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
Jun 02 14:30:29 ymsmmxpruz uvicorn[383815]: INFO:     172.18.0.9:41858 - "GET /health HTTP/1.1" 200 OK
Jun 02 14:32:43 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:32:43,645 INFO Публикация статьи [b432e98b-f3d7-4a7c-a06b-1fea2438fdf2]: Как ChatGPT изменил мою жизнь: честный отзыв после полугода
Jun 02 14:32:44 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:32:44,446 INFO Переход в Студию: https://dzen.ru/profile/editor/create
Jun 02 14:32:49 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:32:49,353 INFO Нажимаем кнопку создания публикации...
Jun 02 14:32:52 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:32:52,327 INFO Выбираем 'Написать статью'...
Jun 02 14:32:58 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:32:58,450 INFO Заполняем редактор (Draft.js)...
Jun 02 14:33:00 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:00,469 INFO Заголовок: Как ChatGPT изменил мою жизнь: честный отзыв после полугода
Jun 02 14:33:02 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:02,359 INFO Картинка 0: https://tempfile.aiquickdraw.com/images/1780392756703-wc49byg454.jpeg
Jun 02 14:33:03 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:03,587 INFO HTTP Request: GET https://tempfile.aiquickdraw.com/images/1780392756703-wc49byg454.jpeg "HTTP/1.1 200 OK"
Jun 02 14:33:03 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:03,977 INFO Найдена кнопка изображения sideButton, кликаем через JS...
Jun 02 14:33:05 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:05,055 INFO URL вставлен в поле диалога, ждём загрузки...
Jun 02 14:33:25 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:25,063 INFO Изображение загружено через URL
Jun 02 14:33:25 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:25,065 INFO Картинка 0 вставлена
Jun 02 14:33:26 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:26,373 INFO Текст после картинки 0: 1029 символов
Jun 02 14:33:27 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:27,597 INFO Картинка 1: https://tempfile.aiquickdraw.com/aistudio/db275125878a5f98ac49c49cd4df4e0f_17803
Jun 02 14:33:28 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:28,448 INFO HTTP Request: GET https://tempfile.aiquickdraw.com/aistudio/db275125878a5f98ac49c49cd4df4e0f_1780392712359.jpeg "HTTP/1.1 200 OK"
Jun 02 14:33:28 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:28,840 INFO Найдена кнопка изображения sideButton, кликаем через JS...
Jun 02 14:33:29 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:29,920 INFO URL вставлен в поле диалога, ждём загрузки...
Jun 02 14:33:49 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:49,926 INFO Изображение загружено через URL
Jun 02 14:33:49 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:49,926 INFO Картинка 1 вставлена
Jun 02 14:33:51 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:51,233 INFO Текст после картинки 1: 2707 символов
Jun 02 14:33:52 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:52,571 INFO Картинка 2: https://tempfile.aiquickdraw.com/r/a3f8d7cdb06691d4b2d8b5061d58fe76_1780392712_x
Jun 02 14:33:53 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:53,323 INFO HTTP Request: GET https://tempfile.aiquickdraw.com/r/a3f8d7cdb06691d4b2d8b5061d58fe76_1780392712_xyq13ko8.jpeg "HTTP/1.1 200 OK"
Jun 02 14:33:53 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:53,675 INFO Найдена кнопка изображения sideButton, кликаем через JS...
Jun 02 14:33:54 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:33:54,756 INFO URL вставлен в поле диалога, ждём загрузки...
Jun 02 14:34:14 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:34:14,761 INFO Изображение загружено через URL
Jun 02 14:34:14 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:34:14,762 INFO Картинка 2 вставлена
Jun 02 14:34:16 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:34:16,071 INFO Текст после картинки 2: 2965 символов
Jun 02 14:35:54 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:35:54,594 INFO Нажимаем 'Опубликовать' (шаг 1)...
Jun 02 14:35:56 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:35:56,747 INFO Финальная кнопка нажата (btn[0])
Jun 02 14:36:18 ymsmmxpruz uvicorn[383815]: 2026-06-02 14:36:18,816 ERROR Ошибка публикации [b432e98b-f3d7-4a7c-a06b-1fea2438fdf2]: publish_error — URL статьи не получен. Текущий URL: https://dzen.ru/profile/editor/id/6a1ac4ce562fd931d75ebe59/6a1ea3447fe8d665fba80ca7/edit
```

---

## Что пробовали для кнопки (не помогло)

1. `page.click(PUBLISH_BUTTON, force=True)` — открывает боковую панель настроек (не попап)
2. `page.wait_for_selector(':not([disabled])', timeout=180s)` — таймаут, кнопка не enabled
3. Поллинг `btn.disabled` каждые 2с до 180с — кнопка никогда не enabled
4. `document.querySelector(...).click()` через JS — React игнорирует клик на disabled
5. `asyncio.sleep(30)` перед кликом — недостаточно
6. `asyncio.sleep(60)` — недостаточно
7. `Ctrl+S` + поллинг 90с — кнопка всё ещё disabled

---

## Ключевые HTML кнопок (от пользователя, вручную в браузере)

**Кнопка 1 — article-publish-btn (ENABLED состояние):**
```html
<button data-testid="article-publish-btn" type="button" tabindex="0">
  <span>Опубликовать</span>
</button>
```

**Кнопка 2 — publish-btn в попапе:**
```html
<button data-testid="publish-btn" type="submit" tabindex="0">
  <span>Опубликовать</span>
</button>
```

**VK Капча в попапе:**
- Текст: "Подтвердите, что вы не робот"
- Чекбокс: "Я не робот"
- После клика чекбокса — публикация должна пройти

---

## Как взаимодействуем / деплой

**Файл редактируется локально**: `d:\CLAUDE CODE\Яндекс Дзен\publisher\dzen.py`  
**Деплой на VPS**: через **Termius SFTP** (не scp, не ssh)  
- Локальный путь: `d:\CLAUDE CODE\Яндекс Дзен\publisher\dzen.py`
- VPS путь: `/opt/dzen-publisher/publisher/dzen.py`

**После деплоя на VPS**:
```bash
systemctl restart dzen-publisher
journalctl -u dzen-publisher -f
```

**Тест публикации** — через n8n workflow: https://n8n.kalininlive.ru/workflow/zYMvxDZyqAl0yNdp  
Или напрямую:
```bash
curl -X POST http://localhost:8001/publish \
  -H "Content-Type: application/json" \
  -d '{"title":"Тест","body":"[IMAGE_0]\nтекст","image_urls":["https://..."]}'
```

---

## Гипотеза причины disabled кнопки

Изображения вставляются через URL в диалоге Дзен-редактора. Дзен-бэкенд пытается скачать изображение с temp URL (`tempfile.aiquickdraw.com`) для сохранения на свой CDN. Если temp URL недоступен или срок истёк — сохранение зависает ("Идёт сохранение" → никогда не "Сохранено") → кнопка остаётся disabled.

**Альтернативная гипотеза**: ClipboardEvent вставка текста после изображений сбрасывает Draft.js save-цикл — Дзен постоянно перезапускает автосохранение.

---

## Текущее состояние кода (`_do_publish`)

```python
# После заполнения редактора:
await page.keyboard.press('Control+s')  # форс-сохранение
await asyncio.sleep(5)
# Поллинг enabled до 90с
for _i in range(18):
    enabled = await page.evaluate("...btn.disabled...")
    if enabled: break
    await asyncio.sleep(5)

# Шаг 1: JS клик article-publish-btn
await page.evaluate("document.querySelector('[data-testid=\"article-publish-btn\"]').click()")
await asyncio.sleep(2)

# Шаг 2: ищем publish-btn или btn[1] из "Опубликовать"
clicked2 = await page.evaluate("""
    const byTestId = document.querySelector('[data-testid="publish-btn"]');
    if (byTestId) { byTestId.click(); return 'publish-btn'; }
    const all = Array.from(document.querySelectorAll('button'))
        .filter(b => b.innerText.trim() === 'Опубликовать');
    if (all.length >= 2) { all[1].click(); return 'btn[1]'; }
    if (all.length === 1) { all[0].click(); return 'btn[0]'; }  // ← сюда попадаем
""")
```

**Результат**: `btn[0]` = кликает disabled `article-publish-btn` → попап не открылся → URL не получен.
