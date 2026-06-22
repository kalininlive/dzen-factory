"""
Dzen Publisher Service — FastAPI entrypoint.

Endpoints:
  POST /publish   — публикует статью через Patchright
  GET  /health    — статус сервиса

Логика дневного лимита:
  Если передан account_id И DATABASE_URL настроен —
    лимит и счётчик берутся из dzen.accounts (Supabase/Postgres).
    Сброс today_count происходит автоматически при первом запросе нового дня.
  Иначе (fallback) —
    используется in-memory счётчик + MAX_DAILY_PUBLICATIONS из .env.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, field_validator

import config
from cookies import cookies_exist
from dzen import DzenPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── DB Pool ───────────────────────────────────────────────────────────────────

_db_pool: Optional[asyncpg.Pool] = None


async def _get_pool() -> Optional[asyncpg.Pool]:
    """Возвращает пул подключений к Postgres, если DATABASE_URL настроен."""
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    if not config.DATABASE_URL:
        return None
    try:
        _db_pool = await asyncpg.create_pool(
            config.DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
        log.info("Postgres пул создан (DATABASE_URL настроен)")
    except Exception as e:
        log.warning("Не удалось подключиться к Postgres: %s. Используется in-memory счётчик.", e)
        _db_pool = None
    return _db_pool


# ── Daily counter (in-memory fallback, resets automatically at midnight) ──────

_daily: dict = {"date": date.today(), "count": 0}


def _get_today_count_memory() -> int:
    """In-memory счётчик: сбрасывается при смене дня (fallback без БД)."""
    if _daily["date"] != date.today():
        _daily["date"] = date.today()
        _daily["count"] = 0
    return _daily["count"]


def _increment_count_memory() -> None:
    _get_today_count_memory()  # resets if new day
    _daily["count"] += 1


# ── DB-backed counter (primary, per account_id) ───────────────────────────────

async def _get_account_limit_db(pool: asyncpg.Pool, account_id: str) -> Optional[dict]:
    """
    Читает из dzen.accounts:
      - today_count — сколько публикаций сделано сегодня
      - daily_limit — максимум на день
      - last_reset  — дата последнего сброса

    Если last_reset < сегодня — сбрасывает today_count = 0 в БД.
    Возвращает {'today_count': int, 'daily_limit': int} или None если аккаунт не найден.
    """
    try:
        today = date.today()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT today_count, daily_limit, last_reset
                FROM dzen.accounts
                WHERE id = $1::uuid
                """,
                account_id,
            )
            if row is None:
                log.warning("Аккаунт %s не найден в dzen.accounts — fallback на in-memory", account_id)
                return None

            # Ленивый сброс счётчика при смене дня
            if row["last_reset"] is None or row["last_reset"] < today:
                await conn.execute(
                    """
                    UPDATE dzen.accounts
                    SET today_count = 0, last_reset = $1, updated_at = NOW()
                    WHERE id = $2::uuid
                    """,
                    today,
                    account_id,
                )
                log.info("Дневной счётчик сброшен для аккаунта %s (был: %s)", account_id, row["today_count"])
                return {"today_count": 0, "daily_limit": row["daily_limit"]}

            return {"today_count": row["today_count"], "daily_limit": row["daily_limit"]}
    except Exception as e:
        log.warning("Ошибка чтения лимита из БД для %s: %s — fallback на in-memory", account_id, e)
        return None


async def _increment_count_db(pool: asyncpg.Pool, account_id: str) -> None:
    """Инкрементирует today_count в dzen.accounts после успешной публикации."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE dzen.accounts
                SET today_count = today_count + 1, updated_at = NOW()
                WHERE id = $1::uuid
                """,
                account_id,
            )
        log.info("today_count инкрементирован для аккаунта %s", account_id)
    except Exception as e:
        log.warning("Не удалось инкрементировать today_count для %s: %s", account_id, e)


# ── Auth ──────────────────────────────────────────────────────────────────────

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


def verify_api_key(key: str = Security(api_key_header)) -> str:
    if not config.PUBLISHER_API_KEY:
        log.warning("PUBLISHER_API_KEY не задан — аутентификация отключена")
        return key
    if key != config.PUBLISHER_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return key


# ── Models ────────────────────────────────────────────────────────────────────

class PublishRequest(BaseModel):
    article_id: str
    account_id: Optional[str] = None
    type: str = "article"  # "article", "post", "video"
    title: Optional[str] = None
    body: str
    image_urls: list[str] = []
    video_url: Optional[str] = None
    cover_url: Optional[str] = None
    scheduled_at: Optional[str] = None

    @field_validator("image_urls", mode="before")
    @classmethod
    def coerce_image_urls(cls, v):
        import json as _json
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s or s == "null":
                return []
            if s.startswith("["):
                try:
                    return _json.loads(s)
                except Exception:
                    pass
            return [s]
        return []


class PublishResponse(BaseModel):
    success: bool
    article_id: str
    published_url: Optional[str] = None
    draft_url: Optional[str] = None   # URL черновика в Дзен — страховка при ошибке публикации
    error: Optional[str] = None
    message: Optional[str] = None
    cookies_valid: bool = True


class AccountCookiesRequest(BaseModel):
    account_id: str
    cookies: list


class AccountCookiesResponse(BaseModel):
    success: bool
    cookies_valid: bool
    error: Optional[str] = None
    message: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    cookies_valid: bool
    proxy_configured: bool
    today_count: int
    daily_limit: int
    limit_source: str  # "database" или "memory"


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Publisher Service запускается (порт %s)", config.PUBLISHER_PORT)
    log.info(
        "Cookies: %s",
        "найдены" if cookies_exist(config.DZEN_COOKIES_PATH) else "ОТСУТСТВУЮТ",
    )
    log.info("Прокси: %s", "настроен" if config.PROXY_URL else "не настроен")
    log.info(
        "Database: %s",
        "настроена" if config.DATABASE_URL else "НЕ НАСТРОЕНА (используется in-memory счётчик)",
    )
    # Инициируем пул при старте (не блокирует запуск если БД недоступна)
    await _get_pool()
    yield
    # Закрываем пул при остановке
    if _db_pool is not None:
        await _db_pool.close()
        log.info("Postgres пул закрыт")
    log.info("Publisher Service останавливается")


app = FastAPI(title="Dzen Publisher Service", lifespan=lifespan)


@app.post("/publish", response_model=PublishResponse)
async def publish(req: PublishRequest, _key: str = Security(verify_api_key)):
    pool = await _get_pool()

    # ── Проверка дневного лимита ────────────────────────────────────────────
    limit_from_db = False
    today_count = 0
    daily_limit = config.MAX_DAILY_PUBLICATIONS

    if pool and req.account_id:
        account_data = await _get_account_limit_db(pool, req.account_id)
        if account_data is not None:
            today_count = account_data["today_count"]
            daily_limit = account_data["daily_limit"]
            limit_from_db = True

    if not limit_from_db:
        # Fallback: in-memory счётчик (нет БД или нет account_id)
        today_count = _get_today_count_memory()
        daily_limit = config.MAX_DAILY_PUBLICATIONS

    if today_count >= daily_limit:
        log.warning(
            "Дневной лимит достигнут (%s/%s) для аккаунта %s [источник: %s]",
            today_count, daily_limit, req.account_id or "—", "DB" if limit_from_db else "memory",
        )
        return PublishResponse(
            success=False,
            article_id=req.article_id,
            error="rate_limited",
            message=f"Дневной лимит {daily_limit} публикаций исчерпан "
                    f"({today_count}/{daily_limit}). Лимит сбросится завтра автоматически.",
            cookies_valid=True,
        )

    # ── Проверка cookies ────────────────────────────────────────────────────
    cookies_dir = os.path.dirname(config.DZEN_COOKIES_PATH)
    cookies_path = (
        os.path.join(cookies_dir, f"{req.account_id}.json")
        if req.account_id
        else config.DZEN_COOKIES_PATH
    )

    if not cookies_exist(cookies_path):
        log.error("Файл cookies для %s не найден: %s", req.account_id or "default", cookies_path)
        return PublishResponse(
            success=False,
            article_id=req.article_id,
            error="session_expired",
            message=f"Файл cookies для аккаунта {req.account_id or 'default'} не найден.",
            cookies_valid=False,
        )

    log.info(
        "Публикация контента [%s] (тип: %s, аккаунт: %s, счётчик: %s/%s [%s]): %s",
        req.article_id, req.type, req.account_id or "default",
        today_count, daily_limit, "DB" if limit_from_db else "memory",
        (req.title or req.body)[:60],
    )

    # ── Публикация ──────────────────────────────────────────────────────────
    async with DzenPublisher(account_id=req.account_id) as publisher:
        result = await publisher.publish(
            content_type=req.type,
            title=req.title,
            body=req.body,
            image_urls=req.image_urls,
            video_url=req.video_url,
            cover_url=req.cover_url,
        )

    if result["success"]:
        # Инкрементируем счётчик в БД или in-memory
        if limit_from_db and pool and req.account_id:
            await _increment_count_db(pool, req.account_id)
        else:
            _increment_count_memory()

        log.info("Опубликовано [%s]: %s", req.article_id, result.get("published_url"))
        return PublishResponse(
            success=True,
            article_id=req.article_id,
            published_url=result.get("published_url"),
            cookies_valid=True,
        )
    else:
        log.error(
            "Ошибка публикации [%s]: %s — %s",
            req.article_id,
            result.get("error"),
            result.get("message"),
        )
        await _maybe_notify_telegram(
            result.get("error", ""),
            result.get("message", ""),
            result.get("draft_url"),
        )

        cookies_valid = result.get("error") != "session_expired"

        return PublishResponse(
            success=False,
            article_id=req.article_id,
            draft_url=result.get("draft_url"),
            error=result.get("error"),
            message=result.get("message"),
            cookies_valid=cookies_valid,
        )


@app.post("/accounts/cookies", response_model=AccountCookiesResponse)
async def save_account_cookies(req: AccountCookiesRequest, _key: str = Security(verify_api_key)):
    from cookies import save_cookies

    cookies_dir = os.path.dirname(config.DZEN_COOKIES_PATH)
    cookies_path = os.path.join(cookies_dir, f"{req.account_id}.json")

    log.info("Сохранение cookies для аккаунта: %s → %s", req.account_id, cookies_path)
    try:
        save_cookies(cookies_path, req.cookies)

        # Проверка валидности сессии
        async with DzenPublisher(account_id=req.account_id) as publisher:
            cookies_valid = await publisher.check_session()

        return AccountCookiesResponse(
            success=True,
            cookies_valid=cookies_valid,
            error=None if cookies_valid else "session_invalid",
            message=None if cookies_valid else "Не удалось войти с предоставленными cookies. Проверьте актуальность cookies.",
        )
    except Exception as e:
        log.error("Ошибка при сохранении или проверке cookies для %s: %s", req.account_id, e)
        return AccountCookiesResponse(
            success=False,
            cookies_valid=False,
            error="save_error",
            message=str(e),
        )


@app.get("/accounts/{account_id}/check")
async def check_account_cookies(account_id: str, _key: str = Security(verify_api_key)):
    cookies_dir = os.path.dirname(config.DZEN_COOKIES_PATH)
    cookies_path = os.path.join(cookies_dir, f"{account_id}.json")

    if not cookies_exist(cookies_path):
        return {
            "account_id": account_id,
            "cookies_valid": False,
            "error": "cookies_file_not_found",
            "message": f"Файл cookies для аккаунта {account_id} не найден.",
        }

    try:
        async with DzenPublisher(account_id=account_id) as publisher:
            cookies_valid = await publisher.check_session()

        return {
            "account_id": account_id,
            "cookies_valid": cookies_valid,
            "error": None if cookies_valid else "session_invalid",
            "message": "Сессия активна" if cookies_valid else "Сессия невалидна. Требуется обновление cookies.",
        }
    except Exception as e:
        log.error("Ошибка при проверке cookies для %s: %s", account_id, e)
        return {
            "account_id": account_id,
            "cookies_valid": False,
            "error": "check_error",
            "message": str(e),
        }


@app.get("/health", response_model=HealthResponse)
async def health():
    has_cookies = cookies_exist(config.DZEN_COOKIES_PATH)
    cookies_valid = False

    if has_cookies:
        try:
            async with DzenPublisher() as publisher:
                cookies_valid = await publisher.check_session()
        except Exception as e:
            log.warning("Ошибка проверки сессии: %s", e)

    # Пробуем получить данные из БД (без account_id — общий статус)
    pool = await _get_pool()
    today_count = _get_today_count_memory()
    daily_limit = config.MAX_DAILY_PUBLICATIONS
    limit_source = "memory"

    if pool:
        try:
            # Возвращаем суммарную статистику по всем активным аккаунтам
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(today_count), 0)::int AS total_today,
                        COALESCE(SUM(daily_limit), 0)::int AS total_limit
                    FROM dzen.accounts
                    WHERE is_active = true
                    """
                )
                if row and row["total_limit"] > 0:
                    today_count = row["total_today"]
                    daily_limit = row["total_limit"]
                    limit_source = "database"
        except Exception as e:
            log.warning("Ошибка чтения статистики из БД: %s", e)

    return HealthResponse(
        status="ok",
        cookies_valid=cookies_valid,
        proxy_configured=bool(config.PROXY_URL),
        today_count=today_count,
        daily_limit=daily_limit,
        limit_source=limit_source,
    )


# ── Telegram notifications ────────────────────────────────────────────────────

async def _maybe_notify_telegram(error_code: str, message: str, draft_url: Optional[str] = None) -> None:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    text_map = {
        "session_expired": "🔑 Cookies Дзена устарели. Запустите `python scripts/save_cookies.py` на VPS.",
        "captcha_detected": "🤖 SmartCaptcha заблокировала публикацию. Проверьте прокси.",
        "editor_not_found": "⚠️ Редактор Дзена не найден — интерфейс мог измениться. Обновите селекторы в dzen.py.",
        "rate_limited": "🚦 Дневной лимит публикаций достигнут.",
    }
    text = text_map.get(error_code, f"❌ Ошибка публикации: {error_code}\n{message}")
    if draft_url:
        text += f"\n\n🔗 Черновик: {draft_url}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
            )
    except Exception as e:
        log.warning("Не удалось отправить Telegram-уведомление: %s", e)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=config.PUBLISHER_PORT, workers=1)
