-- ============================================================
-- Autonomous Yandex Dzen Publishing Platform
-- Полный скрипт инициализации базы данных для Supabase (PostgreSQL)
-- Схема: dzen
-- ============================================================

-- 1. Создание схемы
CREATE SCHEMA IF NOT EXISTS dzen;
COMMENT ON SCHEMA dzen IS 'Основной schema для Yandex Dzen Publishing Platform';

-- 2. Таблица аккаунтов (accounts)
CREATE TABLE IF NOT EXISTS dzen.accounts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        text NOT NULL DEFAULT 'dzen' CHECK (platform IN ('dzen', 'wordpress', 'telegram', 'vc', 'medium')),
    label           text NOT NULL,                    -- Произвольное имя аккаунта (например: "Мой Дзен Блог")
    channel_url     text,                             -- URL канала
    email           text,                             -- Почта аккаунта
    cookie_file     text,                             -- Путь к кукам на диске (устаревает, переходим на БД)
    proxy_url       text,                             -- Прокси для этого аккаунта (protocol://user:pass@host:port)
    daily_limit     int NOT NULL DEFAULT 3,           -- Лимит публикаций в сутки
    today_count     int NOT NULL DEFAULT 0,           -- Опубликовано сегодня
    last_reset      date DEFAULT CURRENT_DATE,        -- Дата последнего сброса лимита
    ya_cookies_encrypted BYTEA,                       -- Зашифрованные cookies Яндекс Паспорта
    dzen_cookies_encrypted BYTEA,                     -- Зашифрованные cookies Дзена
    encryption_version int DEFAULT 1,                 -- Версия ключа шифрования
    api_key         text UNIQUE NOT NULL DEFAULT gen_random_uuid()::text, -- API-ключ для авторизации в n8n/публикаторе
    owner_user_id   uuid,                             -- Владелец аккаунта (ссылка на auth_users)
    is_active       bool NOT NULL DEFAULT true,       -- Активен ли аккаунт
    cookies_valid   bool NOT NULL DEFAULT false,      -- Валидны ли cookies
    cookies_updated_at timestamptz,                   -- Время последнего обновления cookies
    last_error      text,                             -- Текст последней ошибки
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_accounts_api_key ON dzen.accounts(api_key);
CREATE INDEX IF NOT EXISTS idx_accounts_platform ON dzen.accounts(platform);
CREATE INDEX IF NOT EXISTS idx_accounts_is_active ON dzen.accounts(is_active);
CREATE INDEX IF NOT EXISTS idx_accounts_owner_user_id ON dzen.accounts(owner_user_id);
COMMENT ON TABLE dzen.accounts IS 'Аккаунты платформ для публикации и их сессии';

-- 3. Таблица статей (articles)
CREATE TABLE IF NOT EXISTS dzen.articles (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           text NOT NULL,                    -- Исходная тема статьи
    title           text,                             -- Заголовок статьи
    body            text,                             -- Текст (markdown / HTML / текст)
    image_urls      jsonb DEFAULT '[]'::jsonb,        -- Ссылки на картинки статьи
    tags            jsonb DEFAULT '[]'::jsonb,        -- Теги/ключевые слова
    meta_description text,                            -- Краткое описание для SEO (до 160 симв.)
    research_data   jsonb DEFAULT '{}'::jsonb,        -- Результаты исследования темы (Perplexity)
    type            text NOT NULL DEFAULT 'article' CHECK (type IN ('article', 'post', 'video')), -- Тип поста
    video_url       text,                             -- Ссылка на видеофайл (для видеопостов)
    cover_url       text,                             -- Ссылка на обложку видео
    status          text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'generated', 'queued', 'publishing', 'published', 'failed', 'abandoned')),
    platform        text NOT NULL DEFAULT 'dzen',
    account_id      uuid,                             -- FK к dzen.accounts
    scheduled_at    timestamptz,                      -- Время отложенной публикации
    published_at    timestamptz,                      -- Время фактической публикации
    published_url   text,                             -- Ссылка на опубликованный материал
    post_announced  bool NOT NULL DEFAULT false,      -- Анонсирован ли пост в других каналах
    attempts        int NOT NULL DEFAULT 0,           -- Количество попыток публикации
    last_error      text,                             -- Ошибка последней попытки
    next_retry_at   timestamptz,                      -- Время следующей попытки
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_articles_status ON dzen.articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_platform_status ON dzen.articles(platform, status);
CREATE INDEX IF NOT EXISTS idx_articles_account_id ON dzen.articles(account_id);
CREATE INDEX IF NOT EXISTS idx_articles_created_at ON dzen.articles(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_next_retry ON dzen.articles(next_retry_at) WHERE status = 'failed';
COMMENT ON TABLE dzen.articles IS 'Статьи и публикации контент-фабрики';

-- 4. Таблица тем (topics)
CREATE TABLE IF NOT EXISTS dzen.topics (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           text NOT NULL,                    -- Название/тема статьи
    priority        int NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10), -- Приоритет (1 - макс, 10 - мин)
    platform        text NOT NULL DEFAULT 'dzen',
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'done', 'skipped')),
    source          text,                             -- Источник: manual, trends, ai
    article_id      uuid,                             -- Связанная сгенерированная статья (FK)
    notes           text,                             -- Заметки / Ссылки на медиафайлы / Описание
    account_id      uuid,                             -- Аккаунт, для которого создана тема (опционально)
    type            text NOT NULL DEFAULT 'article' CHECK (type IN ('article', 'post', 'video')), -- Ожидаемый тип контента
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_topics_status_priority ON dzen.topics(status, priority) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_topics_type ON dzen.topics(type);
CREATE INDEX IF NOT EXISTS idx_topics_article_id ON dzen.topics(article_id);
COMMENT ON TABLE dzen.topics IS 'Очередь тем для будущих публикаций';

-- 5. Таблица логов публикаций (publish_log)
CREATE TABLE IF NOT EXISTS dzen.publish_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id      uuid NOT NULL,                    -- Ссылка на dzen.articles
    account_id      uuid,                             -- Ссылка на dzen.accounts
    attempt_number  int NOT NULL DEFAULT 1,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    success         bool,
    published_url   text,
    error_code      text,                             -- captcha, session_expired, и др.
    error_message   text,
    duration_ms     int
);

CREATE INDEX IF NOT EXISTS idx_publish_log_article_id ON dzen.publish_log(article_id);
CREATE INDEX IF NOT EXISTS idx_publish_log_account_id ON dzen.publish_log(account_id);
CREATE INDEX IF NOT EXISTS idx_publish_log_created_at ON dzen.publish_log(started_at DESC);
COMMENT ON TABLE dzen.publish_log IS 'Лог всех попыток публикации для отладки';

-- 6. Таблица авторизованных пользователей (auth_users)
CREATE TABLE IF NOT EXISTS dzen.auth_users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username        text NOT NULL UNIQUE,             -- Имя пользователя для Basic Auth
    password_hash   text NOT NULL,                    -- bcrypt хэш пароля
    is_admin        bool NOT NULL DEFAULT false,
    is_active       bool NOT NULL DEFAULT true,
    last_login_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auth_users_username ON dzen.auth_users(username);
CREATE INDEX IF NOT EXISTS idx_auth_users_is_active ON dzen.auth_users(is_active);
COMMENT ON TABLE dzen.auth_users IS 'Пользователи с доступом к панели управления аккаунтами';

-- 7. Таблица логов авторизации (auth_logs)
CREATE TABLE IF NOT EXISTS dzen.auth_logs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid,                             -- FK к dzen.auth_users
    account_id      uuid,                             -- FK к dzen.accounts
    action          text NOT NULL,                    -- login, register_account, validate_cookies, etc.
    ip_address      inet,
    user_agent      text,
    error_code      text,
    error_message   text,
    details         jsonb DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auth_logs_user_id ON dzen.auth_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_logs_action ON dzen.auth_logs(action);
CREATE INDEX IF NOT EXISTS idx_auth_logs_created_at ON dzen.auth_logs(created_at DESC);
COMMENT ON TABLE dzen.auth_logs IS 'Логи авторизации и управления';

-- ============================================================
-- 8. Ограничения внешних ключей (Foreign Keys)
-- ============================================================
ALTER TABLE dzen.articles
    DROP CONSTRAINT IF EXISTS fk_articles_account,
    ADD CONSTRAINT fk_articles_account FOREIGN KEY (account_id)
    REFERENCES dzen.accounts(id) ON DELETE SET NULL;

ALTER TABLE dzen.accounts
    DROP CONSTRAINT IF EXISTS fk_accounts_auth_user,
    ADD CONSTRAINT fk_accounts_auth_user FOREIGN KEY (owner_user_id)
    REFERENCES dzen.auth_users(id) ON DELETE SET NULL;

ALTER TABLE dzen.topics
    DROP CONSTRAINT IF EXISTS fk_topics_article,
    ADD CONSTRAINT fk_topics_article FOREIGN KEY (article_id)
    REFERENCES dzen.articles(id) ON DELETE SET NULL;

ALTER TABLE dzen.topics
    DROP CONSTRAINT IF EXISTS fk_topics_account,
    ADD CONSTRAINT fk_topics_account FOREIGN KEY (account_id)
    REFERENCES dzen.accounts(id) ON DELETE SET NULL;

ALTER TABLE dzen.publish_log
    DROP CONSTRAINT IF EXISTS fk_publish_log_article,
    ADD CONSTRAINT fk_publish_log_article FOREIGN KEY (article_id)
    REFERENCES dzen.articles(id) ON DELETE CASCADE;

ALTER TABLE dzen.publish_log
    DROP CONSTRAINT IF EXISTS fk_publish_log_account,
    ADD CONSTRAINT fk_publish_log_account FOREIGN KEY (account_id)
    REFERENCES dzen.accounts(id) ON DELETE SET NULL;

ALTER TABLE dzen.auth_logs
    DROP CONSTRAINT IF EXISTS fk_auth_logs_user,
    ADD CONSTRAINT fk_auth_logs_user FOREIGN KEY (user_id)
    REFERENCES dzen.auth_users(id) ON DELETE SET NULL;

ALTER TABLE dzen.auth_logs
    DROP CONSTRAINT IF EXISTS fk_auth_logs_account,
    ADD CONSTRAINT fk_auth_logs_account FOREIGN KEY (account_id)
    REFERENCES dzen.accounts(id) ON DELETE SET NULL;

-- ============================================================
-- 9. Триггеры для автоматического обновления updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION dzen.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_articles_updated_at ON dzen.articles;
CREATE TRIGGER trg_articles_updated_at BEFORE UPDATE ON dzen.articles
    FOR EACH ROW EXECUTE FUNCTION dzen.set_updated_at();

DROP TRIGGER IF EXISTS trg_accounts_updated_at ON dzen.accounts;
CREATE TRIGGER trg_accounts_updated_at BEFORE UPDATE ON dzen.accounts
    FOR EACH ROW EXECUTE FUNCTION dzen.set_updated_at();

DROP TRIGGER IF EXISTS trg_topics_updated_at ON dzen.topics;
CREATE TRIGGER trg_topics_updated_at BEFORE UPDATE ON dzen.topics
    FOR EACH ROW EXECUTE FUNCTION dzen.set_updated_at();

DROP TRIGGER IF EXISTS trg_auth_users_updated_at ON dzen.auth_users;
CREATE TRIGGER trg_auth_users_updated_at BEFORE UPDATE ON dzen.auth_users
    FOR EACH ROW EXECUTE FUNCTION dzen.set_updated_at();

-- ============================================================
-- 10. Функции для работы с бизнес-логикой
-- ============================================================

-- Функция для получения данных аккаунта по API Key (для валидации)
CREATE OR REPLACE FUNCTION dzen.get_account_by_api_key(p_api_key TEXT)
RETURNS TABLE (
    id uuid, platform text, label text, channel_url text,
    ya_cookies_encrypted bytea, dzen_cookies_encrypted bytea,
    encryption_version int, is_active bool, cookies_valid bool, daily_limit int
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        a.id, a.platform, a.label, a.channel_url,
        a.ya_cookies_encrypted, a.dzen_cookies_encrypted,
        a.encryption_version, a.is_active, a.cookies_valid, a.daily_limit
    FROM dzen.accounts a
    WHERE a.api_key = p_api_key AND a.is_active = true
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- Функция получения аккаунтов конкретного пользователя
CREATE OR REPLACE FUNCTION dzen.get_user_accounts(p_user_id UUID)
RETURNS TABLE (
    id uuid, label text, platform text, channel_url text,
    api_key text, is_active bool, cookies_valid bool, created_at timestamptz
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        a.id, a.label, a.platform, a.channel_url,
        a.api_key, a.is_active, a.cookies_valid, a.created_at
    FROM dzen.accounts a
    WHERE a.owner_user_id = p_user_id
    ORDER BY a.created_at DESC;
END;
$$ LANGUAGE plpgsql;

-- Сброс дневных лимитов публикаций
CREATE OR REPLACE FUNCTION dzen.reset_daily_counts()
RETURNS void AS $$
BEGIN
    UPDATE dzen.accounts
    SET today_count = 0, last_reset = CURRENT_DATE
    WHERE last_reset < CURRENT_DATE;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- 11. Представление: статьи, готовые к публикации прямо сейчас
-- ============================================================
DROP VIEW IF EXISTS dzen.ready_to_publish CASCADE;
CREATE VIEW dzen.ready_to_publish AS
SELECT
    a.*,
    ac.proxy_url,
    ac.channel_url,
    ac.today_count,
    ac.daily_limit,
    ac.ya_cookies_encrypted,
    ac.dzen_cookies_encrypted,
    ac.encryption_version
FROM dzen.articles a
JOIN dzen.accounts ac ON ac.id = a.account_id
WHERE
    a.status IN ('queued', 'failed')
    AND (a.next_retry_at IS NULL OR a.next_retry_at <= now())
    AND a.attempts < 3
    AND ac.is_active = true
    AND ac.cookies_valid = true
    AND ac.today_count < ac.daily_limit
ORDER BY a.attempts ASC, a.scheduled_at ASC NULLS LAST, a.created_at ASC;

-- ============================================================
-- 12. Безопасность и Права в Supabase
-- ============================================================

-- Отключаем RLS для таблиц (доступ по прямому подключению PostgreSQL / service_role API)
ALTER TABLE dzen.articles DISABLE ROW LEVEL SECURITY;
ALTER TABLE dzen.accounts DISABLE ROW LEVEL SECURITY;
ALTER TABLE dzen.topics DISABLE ROW LEVEL SECURITY;
ALTER TABLE dzen.publish_log DISABLE ROW LEVEL SECURITY;
ALTER TABLE dzen.auth_users DISABLE ROW LEVEL SECURITY;
ALTER TABLE dzen.auth_logs DISABLE ROW LEVEL SECURITY;

-- Гранты ролей для доступа к схеме
GRANT USAGE ON SCHEMA dzen TO postgres, service_role;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA dzen TO postgres, service_role;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA dzen TO postgres, service_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA dzen TO postgres, service_role;

-- Вывод для проверки успешности
SELECT
    'dzen' as schema_name,
    COUNT(*) as table_count
FROM information_schema.tables
WHERE table_schema = 'dzen';
