import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

PUBLISHER_PORT: int = int(os.getenv("PUBLISHER_PORT", "8001"))
PUBLISHER_API_KEY: str = os.getenv("PUBLISHER_API_KEY", "")

DZEN_COOKIES_PATH: str = os.getenv("DZEN_COOKIES_PATH", "./cookies/dzen_cookies.json")
DZEN_CHANNEL_URL: str = os.getenv("DZEN_CHANNEL_URL", "https://dzen.ru/")

PROXY_URL: str = os.getenv("PROXY_URL", "")

# Лимиты (fallback если нет account_id или нет DATABASE_URL)
MAX_DAILY_PUBLICATIONS: int = int(os.getenv("MAX_DAILY_PUBLICATIONS", "3"))
ACTION_DELAY_MIN: float = float(os.getenv("ACTION_DELAY_MIN", "1.5"))
ACTION_DELAY_MAX: float = float(os.getenv("ACTION_DELAY_MAX", "4.0"))

# Прямое подключение к Postgres (Supabase) для чтения/записи today_count и daily_limit
# Формат: postgresql://user:password@host:port/dbname
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
