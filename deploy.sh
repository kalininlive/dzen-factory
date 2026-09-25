#!/usr/bin/env bash

# ============================================================
# Autonomous Yandex Dzen Publishing Platform
# Скрипт автоматического развертывания Publisher Service на VPS
# Поддерживаемые ОС: Ubuntu 22.04 LTS, Ubuntu 24.04 LTS
# ============================================================

set -e

# Цветовая разметка
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0;60m' # No Color
BOLD='\033[1m'

log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step() { echo -e "\n${BLUE}${BOLD}$1${NC}"; }

echo -e "${BLUE}${BOLD}============================================================${NC}"
echo -e "${BLUE}${BOLD}   Dzen Publisher Service Auto-Deploy Script                ${NC}"
echo -e "${BLUE}${BOLD}============================================================${NC}"

step "[1/8] Проверка ОС"
if [ "$EUID" -ne 0 ]; then
  err "Запустите скрипт с правами суперпользователя (sudo)."
fi

if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
    VER=$VERSION_ID
else
    err "Не удалось определить операционную систему."
fi

if [[ "$OS" != "Ubuntu" ]]; then
    err "Поддерживается только Ubuntu. Обнаружено: $OS"
fi

if [[ "$VER" == "22.04" || "$VER" == "24.04" ]]; then
    log "Обнаружена поддерживаемая версия Ubuntu: $VER LTS"
else
    warn "Версия Ubuntu ($VER) не проверялась. Возможны проблемы с совместимостью."
fi

PROJECT_DIR="/opt/dzen-publisher"
REPO_URL="https://github.com/kalininlive/dzen-factory.git"

step "[2/8] Проверка и установка системных зависимостей"
log "Обновление списка пакетов..."
apt-get update -y || err "Не удалось обновить список пакетов (apt-get update)"

log "Установка базовых пакетов (git, python, curl)..."
apt-get install -y python3 python3-pip python3-venv git curl || err "Не удалось установить базовые пакеты"

step "[3/8] Обновление репозитория"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR" || err "Не удалось перейти в директорию $PROJECT_DIR"

if [ -d ".git" ]; then
    log "Репозиторий уже инициализирован."
    log "Обновление кода (git pull)..."
    git fetch origin || err "Ошибка выполнения git fetch"
    git reset --hard origin/main || err "Ошибка выполнения git reset"
else
    log "Клонирование репозитория: $REPO_URL"
    cd /opt || err "Не удалось перейти в /opt"
    rm -rf dzen-publisher
    git clone "$REPO_URL" dzen-publisher || err "Не удалось клонировать репозиторий"
    cd dzen-publisher || err "Не удалось перейти в директорию dzen-publisher"
fi

mkdir -p publisher/cookies || err "Не удалось создать директорию cookies"

step "[4/8] Настройка виртуального окружения Python"
if [ -d ".venv" ]; then
    log "Виртуальное окружение уже существует. Пропуск создания."
else
    log "Создание виртуального окружения..."
    python3 -m venv .venv || err "Не удалось создать виртуальное окружение"
fi

log "Активация виртуального окружения..."
source .venv/bin/activate || err "Не удалось активировать виртуальное окружение"

log "Обновление pip..."
pip install --upgrade pip || err "Не удалось обновить pip"

log "Установка зависимостей из requirements.txt..."
pip install -r publisher/requirements.txt || err "Не удалось установить зависимости Python"

step "[5/8] Установка браузера Chromium (Patchright)"
# Браузер храним в папке проекта, а не в ~/.cache/ms-playwright: чистка кэша на сервере
# (сентябрь 2026) снесла его, публикация и проверки аккаунтов молча падали, а драйверы копились.
export PLAYWRIGHT_BROWSERS_PATH="$PROJECT_DIR/browsers"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
log "Установка системных зависимостей Chromium через patchright..."
patchright install-deps chromium || err "Не удалось установить зависимости Chromium"
log "Установка бинарных файлов Chromium через patchright..."
patchright install chromium || err "Не удалось установить Chromium"

step "[6/8] Конфигурация (.env)"
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env || err "Не удалось скопировать .env.example"
        log "Создан файл .env из шаблона .env.example"
    else
        cat <<EOT > .env
PUBLISHER_PORT=8001
DZEN_COOKIES_PATH=./cookies/dzen_cookies.json
MAX_DAILY_PUBLICATIONS=3
PUBLISHER_API_KEY=your_random_secret_here
EOT
        log "Создан новый файл .env"
    fi
    
    # Генерация ключа если это новый файл
    GENERATED_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/PUBLISHER_API_KEY=.*/PUBLISHER_API_KEY=$GENERATED_KEY/g" .env
    log "Сгенерирован уникальный API-ключ для n8n."
else
    log "Файл .env уже существует. Перезапись пропущена."
    # Пытаемся прочитать существующий ключ для вывода в конце
    GENERATED_KEY=$(grep -E '^PUBLISHER_API_KEY=' .env | cut -d= -f2 || echo "Секретный_ключ_из_.env")
fi

log "Настройка подключения к PostgreSQL (DATABASE_URL)..."
if grep -qE '^DATABASE_URL=' .env 2>/dev/null; then
    log "DATABASE_URL уже задан в .env. Пропуск настройки."
else
    DATABASE_URL_VALUE=""
    SUPABASE_DB_PASS=""

    if command -v docker &>/dev/null; then
        for CONTAINER in supabase-db supabase_db postgres db; do
            PASS=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" 2>/dev/null \
                | grep -E '^POSTGRES_PASSWORD=' | cut -d= -f2 | tr -d '[:space:]' | head -1)
            if [ -n "$PASS" ]; then
                SUPABASE_DB_PASS="$PASS"
                log "Найден пароль Postgres в Docker-контейнере '$CONTAINER'"
                break
            fi
        done
    fi

    if [ -z "$SUPABASE_DB_PASS" ]; then
        for SB_ENV_FILE in /opt/supabase/.env /opt/supabase/docker/.env ~/supabase/.env ~/supabase/docker/.env; do
            if [ -f "$SB_ENV_FILE" ]; then
                PASS=$(grep -E '^POSTGRES_PASSWORD=' "$SB_ENV_FILE" | cut -d= -f2 | tr -d '[:space:]' | head -1)
                if [ -n "$PASS" ]; then
                    SUPABASE_DB_PASS="$PASS"
                    log "Найден пароль Postgres в $SB_ENV_FILE"
                    break
                fi
            fi
        done
    fi

    if [ -n "$SUPABASE_DB_PASS" ]; then
        for PG_HOST in localhost 127.0.0.1 db; do
            if python3 -c "
import socket
try:
    s = socket.create_connection(('$PG_HOST', 5432), timeout=2)
    s.close()
    exit(0)
except:
    exit(1)
" 2>/dev/null; then
                DATABASE_URL_VALUE="postgresql://postgres:${SUPABASE_DB_PASS}@${PG_HOST}:5432/postgres"
                log "PostgreSQL доступен на ${PG_HOST}:5432"
                break
            fi
        done
    fi

    if [ -z "$DATABASE_URL_VALUE" ]; then
        warn "Не удалось автоматически определить DATABASE_URL."
        echo -e "${YELLOW}Введите DATABASE_URL вручную (оставьте пустым чтобы пропустить):${NC}"
        echo -e "  Пример self-hosted: postgresql://postgres:password@localhost:5432/postgres"
        echo -e "  Пример supabase.co:  postgresql://postgres.xxx:pass@aws-region.pooler.supabase.com:6543/postgres"
        read -p "DATABASE_URL: " DATABASE_URL_VALUE
    fi

    if [ -n "$DATABASE_URL_VALUE" ]; then
        echo "DATABASE_URL=$DATABASE_URL_VALUE" >> .env
        log "DATABASE_URL записан в .env"
    else
        warn "DATABASE_URL не задан. Сервис будет использовать in-memory счётчик."
    fi
fi

step "[7/8] Настройка systemd"
SERVICE_FILE="/etc/systemd/system/dzen-publisher.service"

if [ -f "$SERVICE_FILE" ]; then
    log "Служба systemd уже существует. Обновление..."
fi

cat <<EOT > "$SERVICE_FILE" || err "Не удалось записать конфигурацию службы"
[Unit]
Description=Dzen Publisher Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR/publisher
ExecStart=$PROJECT_DIR/.venv/bin/python main.py
Restart=always
RestartSec=5
EnvironmentFile=$PROJECT_DIR/.env

[Install]
WantedBy=multi-user.target
EOT

# Путь к браузеру — drop-in, чтобы переживал ручные правки основного unit-файла
mkdir -p /etc/systemd/system/dzen-publisher.service.d
cat <<EOT > /etc/systemd/system/dzen-publisher.service.d/browsers.conf || err "Не удалось записать drop-in службы"
[Service]
Environment=PLAYWRIGHT_BROWSERS_PATH=$PROJECT_DIR/browsers
EOT
# Защита сервера по памяти: браузер публикатора не должен съесть память соседних сервисов (n8n и т.д.)
cat <<EOT > /etc/systemd/system/dzen-publisher.service.d/limits.conf || err "Не удалось записать лимиты службы"
[Service]
MemoryHigh=1200M
MemoryMax=2G
MemorySwapMax=512M
TasksMax=300
EOT

log "Перезагрузка конфигурации systemd..."
systemctl daemon-reload || err "Не удалось выполнить systemctl daemon-reload"
log "Включение службы..."
systemctl enable dzen-publisher || err "Не удалось включить службу"
log "Перезапуск службы..."
systemctl restart dzen-publisher || err "Не удалось перезапустить службу"

step "[8/8] Проверка сервиса"
sleep 2
if systemctl is-active --quiet dzen-publisher; then
    echo -e "\n${GREEN}${BOLD}✔ Установка успешно завершена! Service is RUNNING.${NC}"
else
    err "Служба не запустилась. Проверьте логи: journalctl -u dzen-publisher -n 50"
fi

echo -e "\n${BLUE}${BOLD}============================================================${NC}"
echo -e "${BLUE}${BOLD}   ДАННЫЕ ДЛЯ НАСТРОЙКИ В n8n (CREDENTIALS):                ${NC}"
echo -e "${BLUE}${BOLD}============================================================${NC}"
echo -e -n "${YELLOW}HTTP Header Auth Name: ${BOLD}X-API-Key${NC}\n"
echo -e -n "${YELLOW}HTTP Header Auth Value (Скопируйте это): ${RED}${BOLD}$GENERATED_KEY${NC}\n"
echo -e "${BLUE}${BOLD}============================================================${NC}"
echo -e "${GREEN}Локальный порт сервиса: http://localhost:8001/health${NC}"
echo -e "${GREEN}Инструкции по обновлению cookies и запуску читайте в README.md${NC}"
echo -e "${BLUE}${BOLD}============================================================${NC}"
