# 🏭 Dzen Factory — Автоматизированный контент-конвейер для Яндекс Дзен

**Dzen Factory** — система автоматической генерации и публикации контента в Яндекс Дзен без участия человека. Написана на Python + n8n, работает на любом VPS.

> **Демо-результаты:** статья с 3 картинками — [dzen.ru/a/aiO_I-Ws_RqZ3sAe](https://dzen.ru/a/aiO_I-Ws_RqZ3sAe) · [dzen.ru/a/aiu1CtlHxhpe-NfP](https://dzen.ru/a/aiu1CtlHxhpe-NfP)

> **💡 Важно:** система изначально ориентирована на **бесплатную генерацию контента**. Из реальных затрат — только оплата VPS-сервера. Однако при желании можно подключить платные сервисы для генерации текста, изображений или видео — система легко расширяется.

---

## Что умеет

| Формат | Описание |
|---|---|
| **Статья** | Лонгрид с подзаголовками, SEO-оптимизацией и встроенными изображениями (от 1 до N штук) |
| **Пост** | Короткая заметка с прикреплёнными изображениями (до 10 штук) |
| **Видео** | Загрузка mp4, автозаполнение названия/описания, установка обложки |

**Дополнительно:**
- 🤖 Исследование тем через AI — поддерживаются Perplexity и другие модели, включая бесплатные
- 🎨 Изображения — Pexels (фотосток, бесплатно) или генерация любой AI-моделью
- 🎭 Антидетект-браузер Patchright — автоматический обход SmartCaptcha
- 👥 Мультиаккаунт — управление несколькими каналами из одной панели
- 📱 Telegram-уведомления о публикациях, ошибках и истечении сессий
- 🔄 Ежедневная автопроверка cookies в 3:00 UTC

---

## Архитектура

```
n8n (оркестратор)
    |-> OpenRouter            — все AI-модели (текст, исследование)
    |-> Pexels / AI-генерация — изображения
    |-> Supabase              — хранение статей, очередь, аккаунты
    |-> Publisher Service     — HTTP API (FastAPI + Python)
            |-> Patchright + Прокси -> Яндекс Дзен
```

**Стек:**

| Компонент | Технология |
|---|---|
| Оркестрация | n8n self-hosted |
| LLM (текст + исследование) | OpenRouter (Claude, OpenAI, Perplexity, DeepSeek, Gemini и др.) |
| Изображения | Pexels API (бесплатно) или любая AI-модель генерации |
| База данных | Supabase (PostgreSQL) |
| Браузер | Patchright (антидетект Playwright) |
| Publisher API | Python 3.10+ / FastAPI |
| Уведомления | Telegram Bot |

---

## Про генерацию контента

### Тексты
Все языковые модели подключаются через **OpenRouter** — единый API-шлюз. Переключайтесь между моделями без изменения кода:
- Бесплатные модели (Claude, Llama, Gemma, Qwen и другие — через OpenRouter бесплатно)
- Платные модели — Claude Sonnet/Opus, GPT-4o, DeepSeek, Gemini и любые другие

### Изображения
Изображения необязательны — система публикует статьи и без них. Варианты:
- **Pexels** — бесплатный фотосток с API (достаточно для большинства тематик)
- **AI-генерация** — подключается любая модель (Nano Banana 2, GPT Image 1 и другие)

### Исследование тем
Для поиска актуальной информации перед написанием статьи можно использовать Perplexity (через OpenRouter) или другие модели с доступом к интернету, в том числе бесплатные.

---

## Структура репозитория

```
|-- publisher/
|   |-- main.py             # FastAPI: /publish, /health, /accounts
|   |-- dzen.py             # Браузерная автоматизация Дзена
|   |-- cookies.py          # Загрузка и валидация cookies
|   |-- config.py           # Переменные окружения
|   |-- requirements.txt
|-- n8n-workflows/          # JSON-экспорты воркфлоу n8n
|-- supabase/
|   |-- schema.sql
|-- scripts/
|   |-- save_cookies.py     # Утилита ручного сохранения cookies
|-- setup_all.sql           # Полная схема БД для Supabase
|-- deploy.sh               # Скрипт автодеплоя на VPS
|-- .env.example            # Шаблон переменных окружения
```

---

## Требования

- VPS: Ubuntu 20.04+ / Debian 11+ (минимум 2 GB RAM, 1 vCPU)
- Python 3.10+
- n8n self-hosted (можно на том же VPS)
- Аккаунт Supabase (бесплатный tier достаточен)
- Яндекс-аккаунт с каналом на Дзене

### Необходимые сервисы

| Сервис | Где зарегистрироваться | Для чего | Стоимость |
|---|---|---|---|
| **Supabase** | [supabase.com](https://supabase.com) | База данных | Бесплатно |
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) | Все AI-модели (текст, исследование) | Есть бесплатные модели |
| **Pexels API** | [pexels.com/api](https://www.pexels.com/api/) | Изображения из фотостока | Бесплатно |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | Генерация изображений (опционально) | Платно |
| **Telegram @BotFather** | [@BotFather](https://t.me/BotFather) в Telegram | Уведомления | Бесплатно |

---

## Установка

### Шаг 1 — База данных (Supabase)

1. Войдите в [supabase.com](https://supabase.com) и откройте ваш проект.
2. Перейдите в **SQL Editor → New Query**.
3. Вставьте содержимое файла **[setup_all.sql](setup_all.sql)** и нажмите **Run**.
4. Ожидаемый результат:
   ```
   schema_name | table_count
   -------------+-------------
   dzen        |           6
   ```
5. Скопируйте из **Project Settings → API**:
   - `Project URL`
   - `anon public key`
   - `service_role key`

### Какие таблицы создаются

| Таблица | Назначение |
|---|---|
| `dzen.accounts` | Аккаунты Дзена (канал, лимиты, статус сессии) |
| `dzen.topics` | Очередь тем для публикации |
| `dzen.articles` | Готовые сгенерированные статьи |
| `dzen.publish_log` | Лог всех публикаций |
| `dzen.images` | URL сгенерированных изображений |
| `dzen.settings` | Глобальные настройки |

---

### Шаг 2 — Publisher Service на VPS

Подключитесь к серверу и выполните одну команду:

```bash
mkdir -p /opt/dzen-publisher && cd /opt/dzen-publisher && \
  curl -sSL https://raw.githubusercontent.com/kalininlive/dzen-factory/main/deploy.sh | sudo bash
```

> Установка занимает 5–15 минут (скачивается браузер Chromium ~150 MB).

**Что делает скрипт:**

| Шаг | Действие |
|---|---|
| [1/6] | Создаёт папку `/opt/dzen-publisher/` |
| [2/6] | Клонирует репозиторий из GitHub |
| [3/6] | Устанавливает Python 3 и системные библиотеки для браузера |
| [4/6] | Создаёт виртуальное окружение `.venv`, устанавливает зависимости, скачивает Chromium |
| [5/6] | Создаёт `.env`, **генерирует уникальный API-ключ** |
| [6/6] | Создаёт и запускает службу systemd `dzen-publisher` |

В конце установки скрипт выведет API-ключ — **скопируйте его**:
```
============================================================
   ДАННЫЕ ДЛЯ НАСТРОЙКИ В n8n (CREDENTIALS):
============================================================
HTTP Header Auth Name:  X-API-Key
HTTP Header Auth Value: d7a67f0c...  <- СКОПИРОВАТЬ
============================================================
```

**Проверка:**
```bash
curl http://localhost:8001/health
# {"status":"ok","cookies_valid":false}
```

---

### Шаг 2.5 — Подключение к PostgreSQL

Чтобы дневной лимит публикаций хранился в Supabase (а не сбрасывался при перезапуске), задайте `DATABASE_URL`:

```bash
nano /opt/dzen-publisher/.env
```

**Облачный Supabase:**
```env
DATABASE_URL=postgresql://postgres.XXXX:[PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
```
> Строку берите из **Project Settings → Database → Transaction pooler**.

**Self-hosted Supabase:**
```env
DATABASE_URL=postgresql://postgres:ПАРОЛЬ@localhost:5432/postgres
```

```bash
systemctl restart dzen-publisher
# Проверка: curl http://localhost:8001/health
# Должно появиться "limit_source": "database"
```

---

### Шаг 3 — Сохранение cookies Дзена

Авторизация в Дзен требуется **один раз вручную** (из-за OTP).

**Способ — расширение Cookie-Editor:**

1. Установите [Cookie-Editor](https://cookie-editor.com) в браузер
2. Войдите в свой аккаунт на [dzen.ru](https://dzen.ru)
3. Откройте Cookie-Editor → **Export → Export as JSON**
4. Загрузите файл на VPS:

```bash
# Выполнить на ВАШЕМ компьютере:
scp ~/Downloads/cookies.json root@ВАШ_IP:/opt/dzen-publisher/publisher/cookies/dzen_cookies.json
```

**Проверка:**
```bash
curl http://localhost:8001/health
# "cookies_valid": true
```

---

### Шаг 4 — Настройка n8n

**Credentials → Add Credential → HTTP Header Auth** (для Publisher):
- Name: `X-API-Key`
- Value: `<API-ключ из Шага 2>`

**Credentials для AI и сервисов:**

| Сервис | Тип Credential в n8n | Что вводить |
|---|---|---|
| OpenRouter | HTTP Header Auth | Name: `Authorization`, Value: `Bearer ВАШ_OPENROUTER_KEY` |
| OpenAI | OpenAI API | `OPENAI_API_KEY` (если нужна генерация изображений) |
| Pexels | HTTP Header Auth | Name: `Authorization`, Value: `ВАШ_PEXELS_API_KEY` |
| Telegram Bot | Telegram Bot API | `TELEGRAM_BOT_TOKEN` |
| Supabase | Supabase API | URL + service_role key |

**URL Publisher из Docker n8n:**
```bash
# Узнайте IP на VPS:
ip route | grep docker
# Используйте IP шлюза, например: http://172.17.0.1:8001
```

---

## Структура файлов на VPS после установки

```
/opt/dzen-publisher/
|-- .env                            <- настройки (секреты, не публичный файл)
|-- .env.example                    <- шаблон
|-- .venv/                          <- виртуальное окружение Python
|
|-- publisher/
|   |-- main.py                     <- HTTP API
|   |-- dzen.py                     <- браузерная автоматизация
|   |-- cookies.py
|   |-- config.py
|   |-- requirements.txt
|   |-- cookies/
|       |-- dzen_cookies.json       <- cookies основного аккаунта
|       |-- UUID.json               <- cookies для мультиаккаунта
|
|-- scripts/
    |-- save_cookies.py
```

---

## Переменные окружения (.env)

Файл: `/opt/dzen-publisher/.env`

```env
# Сервис
PUBLISHER_PORT=8001
PUBLISHER_API_KEY=d7a67f0c...
# Секретный ключ. Генерируется автоматически при установке.

# База данных
DATABASE_URL=postgresql://postgres.XXXX:pass@host:6543/postgres
# Строка подключения к Supabase.
# Без неё — лимиты в памяти (сбросятся при перезапуске).

# Лимиты (fallback без БД)
MAX_DAILY_PUBLICATIONS=3

# Задержки браузера (имитация человека)
ACTION_DELAY_MIN=1.5
ACTION_DELAY_MAX=4.0

# Прокси (рекомендуется для продакшена)
PROXY_URL=http://user:password@proxy.host:port
# Без прокси при частых публикациях может появиться капча.
# Мобильные резидентные прокси — оптимальный вариант.
```

---

## Publisher API

### POST /publish

Заголовок аутентификации: `X-API-Key: ВАШ_КЛЮЧ`

```json
{
  "article_id": "uuid",
  "account_id": "uuid",
  "type": "article",
  "title": "Заголовок",
  "body": "<p>Текст...</p>[DZEN_IMAGE]<p>Продолжение...</p>",
  "image_urls": [
    "https://example.com/cover.jpg",
    "https://example.com/image1.jpg"
  ],
  "video_url": null,
  "cover_url": null,
  "scheduled_at": null
}
```

| Поле | Тип | Описание |
|---|---|---|
| `type` | string | `"article"` / `"post"` / `"video"` (по умолчанию `"article"`) |
| `body` | string | HTML-текст. Маркер `[DZEN_IMAGE]` — место встройки изображения |
| `image_urls` | array | Первый URL — обложка. Остальные — по маркерам `[DZEN_IMAGE]` |
| `video_url` | string | Путь к mp4 на VPS (для `type: "video"`) |
| `account_id` | string | UUID аккаунта (для мультиаккаунта) |

**Ответ:**
```json
{"status": "ok", "url": "https://dzen.ru/a/abc123"}
```

### GET /health

```json
{"status": "ok", "cookies_valid": true, "limit_source": "database"}
```

### GET /accounts/{account_id}/check

Проверяет валидность сессии аккаунта.

---

## Управление службой

```bash
systemctl status dzen-publisher        # статус
systemctl restart dzen-publisher       # перезапуск
journalctl -u dzen-publisher -f        # логи в реальном времени
journalctl -u dzen-publisher -n 100    # последние 100 строк
```

**Обновление кода с GitHub:**
```bash
cd /opt/dzen-publisher && git pull && systemctl restart dzen-publisher
```

Проверить что версия обновилась:
```bash
cd /opt/dzen-publisher && git log --oneline -5
```

> Файл `.env` при `git pull` **не перезаписывается** — ваши настройки сохраняются.

---

## Мультиаккаунт

```sql
-- Добавить аккаунт
INSERT INTO dzen.accounts (id, name, channel_url, daily_limit, is_active)
VALUES (gen_random_uuid(), 'Мой канал 2', 'https://dzen.ru/canal2', 3, true)
RETURNING id;
-- Скопируйте UUID -> назовите им файл cookies

-- Изменить лимит
UPDATE dzen.accounts SET daily_limit = 5 WHERE name = 'Мой канал 2';

-- Добавить тему
INSERT INTO dzen.topics (account_id, topic, type, notes)
VALUES ('UUID', 'Тема статьи', 'article', 'Доп. указания для AI');
```

Загрузить cookies аккаунта:
```bash
scp cookies.json root@VPS:/opt/dzen-publisher/publisher/cookies/UUID.json
```

---

## Решение проблем

**Сервис не запускается**
```bash
journalctl -u dzen-publisher -n 50
```
Частые причины: ошибка в `.env`, порт 8001 занят, не установлены зависимости.

**`cookies_valid: false`** — сессия устарела, повторите Шаг 3.

**SmartCaptcha** — настройте `PROXY_URL` (мобильные резидентные прокси) и уменьшите частоту публикаций.

**n8n не достучится до сервиса** — Publisher должен слушать `0.0.0.0`, а не `127.0.0.1`. Найдите правильный Docker IP:
```bash
ip route | grep docker
```

**Сломались кнопки в Дзене** — интерфейс обновился. Актуальные селекторы: [DZEN.md](DZEN.md).

**Обновить cookies без перезапуска:**
```bash
scp new_cookies.json root@VPS:/opt/dzen-publisher/publisher/cookies/dzen_cookies.json
# Перезапуск НЕ нужен — файл читается при каждой публикации
```

---

## Покупка готовых воркфлоу n8n

В репозитории находится **инфраструктурная часть** (БД + Publisher Service).  
**Готовые, настроенные воркфлоу n8n — приобретаются отдельно.**

| Шаблон | Описание |
|---|---|
| **Dzen Content Factory** | Полный конвейер: тема -> исследование -> текст -> изображения -> публикация -> анонс -> Telegram |
| **Dzen Account Manager** | SPA-панель управления каналами внутри n8n: добавление аккаунтов, cookies, лимиты, автопроверка |

> Telegram: [@websansay](https://t.me/websansay) — покупка, кастомизация, поддержка  
> Кейсы и примеры: [t.me/+VxXC2TaMEv0zMzcy](https://t.me/+VxXC2TaMEv0zMzcy)

---

## Лицензия

MIT — используйте свободно. Соблюдайте [правила Яндекс Дзена](https://yandex.ru/legal/zen_rules/).
