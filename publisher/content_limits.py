"""
Лимиты контента Яндекс Дзен по типам публикаций + серверная нормализация.

Источник истины по лимитам — ЗДЕСЬ, а не в LLM или в n8n.
Любой текст обрезается до лимита ПЕРЕД отправкой в браузер,
чтобы гарантировать соответствие требованиям Дзена независимо от того,
что прислал воркфлоу (LLM регулярно промахивается на несколько символов).

Типы публикаций:
  article — статья: есть заголовок, большое тело.
  post    — пост: без заголовка, короткое тело.
  video   — обычное (горизонтальное) видео: заголовок ≤140, описание ≤5000,
            обложка ОБЯЗАТЕЛЬНА, поддерживаются теги.
  reel    — ролик (вертикальное короткое видео): БЕЗ заголовка,
            описание ≤200, обложка не нужна.
"""

from typing import Optional


# Жёсткие лимиты Дзена по типам публикаций.
#   title = None → у типа нет отдельного поля заголовка (обнуляем).
#   desc         → максимум символов описания/тела.
#   cover        → нужна ли обложка (True → обязательна).
#   tags         → поддерживаются ли теги.
CONTENT_LIMITS: dict[str, dict] = {
    "article": {"title": 100,  "desc": 100_000, "cover": False, "tags": True},
    "post":    {"title": None, "desc": 4_000,   "cover": False, "tags": False},
    "video":   {"title": 140,  "desc": 5_000,   "cover": True,  "tags": True},
    "reel":    {"title": None, "desc": 200,     "cover": False, "tags": False},
}

DEFAULT_TYPE = "article"


class ContentValidationError(ValueError):
    """Контент не удовлетворяет обязательным требованиям типа (например, video без обложки)."""


def get_limits(content_type: str) -> dict:
    return CONTENT_LIMITS.get(content_type, CONTENT_LIMITS[DEFAULT_TYPE])


def clamp_text(text: Optional[str], limit: Optional[int]) -> str:
    """Обрезает текст до `limit` символов по границе слова.

    limit=None или текст короче лимита → возвращает текст без хвостовых пробелов.
    """
    text = (text or "").strip()
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit]
    # режем по последнему пробелу в хвосте, чтобы не обрывать слово на полуслове
    trimmed = cut.rstrip()
    last_space = trimmed.rfind(" ")
    if last_space > limit * 0.6:  # не режем слишком агрессивно, если пробел далеко
        cut = trimmed[:last_space]
    return cut.rstrip(" ,.;:—–-\n\t")


def normalize_content(
    content_type: str,
    title: Optional[str],
    body: Optional[str],
    cover_url: Optional[str] = None,
) -> dict:
    """Приводит title/body к лимитам типа и проверяет обязательные поля.

    Возвращает dict: {type, title, body, meta:{title_len, body_len, limits}}.
    Бросает ContentValidationError при нарушении обязательных требований.
    """
    limits = get_limits(content_type)

    # Заголовок: если у типа нет поля заголовка (post, reel) — обнуляем.
    if limits["title"] is None:
        norm_title: Optional[str] = None
    else:
        norm_title = clamp_text(title, limits["title"]) or None

    norm_body = clamp_text(body, limits["desc"])

    # Обязательная обложка (video).
    if limits["cover"] and not cover_url:
        raise ContentValidationError(
            f"Тип '{content_type}' требует обложку (cover_url), но она не передана."
        )

    return {
        "type": content_type,
        "title": norm_title,
        "body": norm_body,
        "meta": {
            "title_len": len(norm_title) if norm_title else 0,
            "body_len": len(norm_body),
            "limits": limits,
        },
    }
