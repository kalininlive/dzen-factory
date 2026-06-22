-- ============================================================
-- Autonomous Yandex Dzen Publishing Platform
-- Supabase Schema — dzen schema
-- v2: синхронизирована с реальной БД (image_urls, account_id в topics, миграции)
-- ============================================================
CREATE SCHEMA IF NOT EXISTS dzen;

-- ============================================================
-- dzen.articles — статьи (главная таблица)
-- ============================================================
CREATE TABLE IF NOT EXISTS dzen.articles (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           text NOT NULL,                    -- исходная тема (для генерации)
    title           text,                             -- сгенерированный заголовок
    body            text,                             -- текст статьи (markdown)
    image_urls      jsonb DEFAULT '[]'::jsonb,        -- массив URL изображений (основной)
    images          jsonb DEFAULT '[]'::jsonb,        -- алиас для совместимости
    tags            jsonb DEFAULT '[]'::jsonb,        -- теги/ключевые слова
    meta_description text,                            -- краткое описание (160 символов)
    research_data   jsonb DEFAULT '{}'::jsonb,        -- данные Perplexity (факты, источники)
    type            text NOT NULL DEFAULT 'article'
                    CHECK (type IN ('article', 'post', 'video')),
    video_url       text,                             -- URL видеоролика (для видеопостов)
    cover_url       text,                             -- URL обложки видео

    -- Статус прохождения пайплайна
    status          text NOT NULL DEFAULT 'draft'
                    CHECK (status IN (
                        'draft',        -- только тема, контент не сгенерирован
                        'generated',    -- контент сгенерирован, ожидает публикации
                        'queued',       -- поставлен в очередь на публикацию
                        'publishing',   -- Publisher Service обрабатывает прямо сейчас
                        'published',    -- успешно опубликован
                        'failed',       -- ошибка, будет повторная попытка
                        'abandoned'     -- 3 попытки провалились, требуется ручное вмешательство
                    )),

    -- Публикация
    platform        text NOT NULL DEFAULT 'dzen'
                    CHECK (platform IN ('dzen', 'wordpress', 'telegram', 'vc', 'medium')),
    account_id      uuid,                             -- FK → dzen.accounts
    scheduled_at    timestamptz,                      -- запланированное время публикации
    published_at    timestamptz,                      -- фактическое время публикации
    published_url   text,                             -- URL опубликованной статьи
    post_announced  bool DEFAULT false,               -- был ли опубликован пост-анонс

    -- Очередь и ошибки
    attempts        int NOT NULL DEFAULT 0,           -- количество попыток публикации
    last_error      text,                             -- текст последней ошибки
    next_retry_at   timestamptz,                      -- время следующей попытки

    -- Метаданные
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE dzen.articles IS 'Статьи контент-фабрики: от темы до опубликованного материала';

-- ============================================================
-- dzen.accounts — аккаунты для публикации
-- ============================================================
CREATE TABLE IF NOT EXISTS dzen.accounts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        text NOT NULL DEFAULT 'dzen'
                    CHECK (platform IN ('dzen', 'wordpress', 'telegram', 'vc', 'medium')),
    label           text NOT NULL,                    -- произвольное имя аккаунта
    channel_url     text,                             -- URL канала/сайта
    cookie_file     text,                             -- путь к файлу cookies на VPS
    proxy_url       text,                             -- прокси для этого аккаунта

    -- Лимиты
    daily_limit     int NOT NULL DEFAULT 3,           -- максимум публикаций в день
    today_count     int NOT NULL DEFAULT 0,           -- опубликовано сегодня
    last_reset      date DEFAULT CURRENT_DATE,        -- дата последнего сброса счётчика

    -- Статус
    is_active       bool NOT NULL DEFAULT true,
    cookies_valid   bool NOT NULL DEFAULT false,      -- сессия актуальна?
    cookies_updated_at timestamptz,                   -- когда последний раз обновляли cookies
    last_error      text,

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE dzen.accounts IS 'Аккаунты платформ для публикации (Дзен, WordPress и др.)';

-- ============================================================
-- dzen.topics — очередь тем для будущих статей
-- ============================================================
CREATE TABLE IF NOT EXISTS dzen.topics (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           text NOT NULL,                    -- формулировка темы
    priority        int NOT NULL DEFAULT 5            -- 1=высокий, 10=низкий
                    CHECK (priority BETWEEN 1 AND 10),
    platform        text NOT NULL DEFAULT 'dzen',
    account_id      uuid,                             -- FK → dzen.accounts
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'in_progress', 'done', 'skipped')),
    source          text,                             -- откуда тема: 'manual', 'trends', 'ai'
    article_id      uuid,                             -- FK → dzen.articles (после генерации)
    notes           text,                             -- заметки/контекст для генерации
    type            text NOT NULL DEFAULT 'article'
                    CHECK (type IN ('article', 'post', 'video')),

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE dzen.topics IS 'Очередь тем для генерации статей';

-- ============================================================
-- dzen.publish_log — лог всех попыток публикации
-- ============================================================
CREATE TABLE IF NOT EXISTS dzen.publish_log (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id      uuid NOT NULL,                    -- FK → dzen.articles
    account_id      uuid,                             -- FK → dzen.accounts
    attempt_number  int NOT NULL DEFAULT 1,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    success         bool,
    published_url   text,
    error_code      text,                             -- 'session_expired', 'captcha', etc.
    error_message   text,
    duration_ms     int                               -- сколько миллисекунд заняла публикация
);

COMMENT ON TABLE dzen.publish_log IS 'Детальный лог всех попыток публикации для отладки';

-- ============================================================
-- FK constraints (безопасные — через DO блоки)
-- ============================================================
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_articles_account') THEN
        ALTER TABLE dzen.articles
            ADD CONSTRAINT fk_articles_account
            FOREIGN KEY (account_id) REFERENCES dzen.accounts(id) ON DELETE SET NULL;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_topics_article') THEN
        ALTER TABLE dzen.topics
            ADD CONSTRAINT fk_topics_article
            FOREIGN KEY (article_id) REFERENCES dzen.articles(id) ON DELETE SET NULL;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_topics_account') THEN
        ALTER TABLE dzen.topics
            ADD CONSTRAINT fk_topics_account
            FOREIGN KEY (account_id) REFERENCES dzen.accounts(id) ON DELETE SET NULL;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_publish_log_article') THEN
        ALTER TABLE dzen.publish_log
            ADD CONSTRAINT fk_publish_log_article
            FOREIGN KEY (article_id) REFERENCES dzen.articles(id) ON DELETE CASCADE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_publish_log_account') THEN
        ALTER TABLE dzen.publish_log
            ADD CONSTRAINT fk_publish_log_account
            FOREIGN KEY (account_id) REFERENCES dzen.accounts(id) ON DELETE SET NULL;
    END IF;
END $$;



-- ============================================================
-- Индексы
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_articles_status           ON dzen.articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_platform_status  ON dzen.articles(platform, status);
CREATE INDEX IF NOT EXISTS idx_articles_scheduled_at     ON dzen.articles(scheduled_at) WHERE scheduled_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_articles_next_retry       ON dzen.articles(next_retry_at) WHERE status = 'failed';
CREATE INDEX IF NOT EXISTS idx_articles_created_at       ON dzen.articles(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_topics_status_priority    ON dzen.topics(status, priority) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_topics_account            ON dzen.topics(account_id) WHERE account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_publish_log_article       ON dzen.publish_log(article_id, started_at DESC);

-- ============================================================
-- Автообновление updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION dzen.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_articles_updated_at
    BEFORE UPDATE ON dzen.articles
    FOR EACH ROW EXECUTE FUNCTION dzen.set_updated_at();

CREATE OR REPLACE TRIGGER trg_accounts_updated_at
    BEFORE UPDATE ON dzen.accounts
    FOR EACH ROW EXECUTE FUNCTION dzen.set_updated_at();

CREATE OR REPLACE TRIGGER trg_topics_updated_at
    BEFORE UPDATE ON dzen.topics
    FOR EACH ROW EXECUTE FUNCTION dzen.set_updated_at();

-- ============================================================
-- Сброс дневного счётчика (вызывать из n8n в 00:00)
-- ============================================================
CREATE OR REPLACE FUNCTION dzen.reset_daily_counts()
RETURNS void AS $$
BEGIN
    UPDATE dzen.accounts
    SET today_count = 0,
        last_reset  = CURRENT_DATE
    WHERE last_reset < CURRENT_DATE;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION dzen.reset_daily_counts() IS
    'Сбрасывает счётчик today_count для аккаунтов, которые не сбрасывались сегодня. Вызывать из n8n в 00:01 по расписанию.';

-- ============================================================
-- View: статьи готовые к публикации прямо сейчас
-- ============================================================
DROP VIEW IF EXISTS dzen.ready_to_publish CASCADE;
CREATE OR REPLACE VIEW dzen.ready_to_publish AS
SELECT
    a.*,
    ac.cookie_file,
    ac.proxy_url,
    ac.channel_url,
    ac.today_count,
    ac.daily_limit
FROM dzen.articles a
JOIN dzen.accounts ac ON ac.id = a.account_id
WHERE
    a.status IN ('queued', 'failed')
    AND (a.next_retry_at IS NULL OR a.next_retry_at <= now())
    AND a.attempts < 3
    AND ac.is_active = true
    AND ac.cookies_valid = true
    AND ac.today_count < ac.daily_limit
ORDER BY
    a.attempts ASC,
    a.scheduled_at ASC NULLS LAST,
    a.created_at ASC;

COMMENT ON VIEW dzen.ready_to_publish IS
    'Статьи готовые к публикации: очередь + провальные (с retry), с данными аккаунта';

-- ============================================================
-- Начальные данные: аккаунт по умолчанию
-- ============================================================
INSERT INTO dzen.accounts (platform, label, channel_url, cookie_file, daily_limit, is_active)
VALUES ('dzen', 'Основной аккаунт', 'https://dzen.ru/', './cookies/dzen_cookies.json', 3, false)
ON CONFLICT DO NOTHING;
