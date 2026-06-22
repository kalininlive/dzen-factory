#!/usr/bin/env bash

# ============================================================
# Autonomous Yandex Dzen Publishing Platform
# Скрипт автоматического развертывания Publisher Service на VPS
# Поддерживаемые ОС: Ubuntu 20.04+, Debian 11+
# ============================================================

set -e

# Цветовая разметка
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0;60m' # No Color
BOLD='\033[1m'

echo -e "${BLUE}${BOLD}============================================================${NC}"
echo -e "${BLUE}${BOLD}   Dzen Publisher Service Auto-Deploy Script                ${NC}"
echo -e "${BLUE}${BOLD}============================================================${NC}"

# Проверка прав root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Ошибка: Запустите скрипт с правами суперпользователя (sudo).${NC}"
  exit 1
fi

PROJECT_DIR="/opt/dzen-publisher"
REPO_URL="https://github.com/kalininlive/dzen-factory.git"

# 1. Создание рабочей директории
echo -e "\n${YELLOW}[1/6] Подготовка директорий...${NC}"
mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

# 2. Проверка Git и загрузка кода
echo -e "\n${YELLOW}[2/6] Загрузка файлов из репозитория...${NC}"
if [ -d ".git" ]; then
    echo -e "${GREEN}Репозиторий уже инициализирован. Обновляем код...${NC}"
    git pull || echo -e "${YELLOW}Предупреждение: Не удалось выполнить git pull. Продолжаем с текущей версией.${NC}"
else
    echo -e "${GREEN}Клонирование репозитория: $REPO_URL${NC}"
    cd /opt
    rm -rf dzen-publisher
    git clone "$REPO_URL" dzen-publisher
    cd dzen-publisher
fi


# Создаем нужные подпапки
mkdir -p publisher/cookies

# 3. Установка системных зависимостей
echo -e "\n${YELLOW}[3/6] Установка системных пакетов (Python, venv, curl)...${NC}"
apt-get update -y
apt-get install -y python3 python3-pip python3-venv git curl libgconf-2-4 libatk1.0-0 libatk-bridge2.0-0 libgdk-pixbuf2.0-0 libgtk-3-0 libgbm-dev libnss3 libasound2

# 4. Настройка виртуального окружения Python
echo -e "\n${YELLOW}[4/6] Настройка виртуального окружения Python...${NC}"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r publisher/requirements.txt

# Установка Patchright браузера и его зависимостей
echo -e "${GREEN}Установка браузера Chromium и зависимостей...${NC}"
patchright install chromium
patchright install-deps chromium

# 5. Генерация файла конфигурации .env и API ключа
echo -e "\n${YELLOW}[5/6] Настройка переменных окружения (.env)...${NC}"
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}Создан файл .env из шаблона .env.example${NC}"
    else
        # Создаем минимальный .env
        cat <<EOT > .env
PUBLISHER_PORT=8001
DZEN_COOKIES_PATH=./cookies/dzen_cookies.json
MAX_DAILY_PUBLICATIONS=3
PUBLISHER_API_KEY=your_random_secret_here
EOT
        echo -e "${GREEN}Создан новый файл .env${NC}"
    fi
fi

# Автоматическая генерация уникального PUBLISHER_API_KEY
GENERATED_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
sed -i "s/PUBLISHER_API_KEY=.*/PUBLISHER_API_KEY=$GENERATED_KEY/g" .env
echo -e "${GREEN}Сгенерирован уникальный API-ключ для n8n.${NC}"

# Автоопределение DATABASE_URL для хранения дневного счётчика в Supabase
echo -e "\n${YELLOW}[Доп] Настройка подключения к PostgreSQL (DATABASE_URL)...${NC}"

DATABASE_URL_VALUE=""

# --- Попытка 1: self-hosted Supabase (Docker) ---
# Ищем пароль в docker-compose или в .env самого Supabase
SUPABASE_DB_PASS=""

if command -v docker &>/dev/null; then
    # Пытаемся получить пароль из переменных контейнера supabase-db
    for CONTAINER in supabase-db supabase_db postgres db; do
        PASS=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" 2>/dev/null \
            | grep -E '^POSTGRES_PASSWORD=' | cut -d= -f2 | tr -d '[:space:]' | head -1)
        if [ -n "$PASS" ]; then
            SUPABASE_DB_PASS="$PASS"
            echo -e "${GREEN}Найден пароль Postgres в Docker-контейнере '$CONTAINER'${NC}"
            break
        fi
    done
fi

# Если Docker не дал — ищем в /opt/supabase/.env или ~/supabase/.env
if [ -z "$SUPABASE_DB_PASS" ]; then
    for SB_ENV_FILE in /opt/supabase/.env /opt/supabase/docker/.env ~/supabase/.env ~/supabase/docker/.env; do
        if [ -f "$SB_ENV_FILE" ]; then
            PASS=$(grep -E '^POSTGRES_PASSWORD=' "$SB_ENV_FILE" | cut -d= -f2 | tr -d '[:space:]' | head -1)
            if [ -n "$PASS" ]; then
                SUPABASE_DB_PASS="$PASS"
                echo -e "${GREEN}Найден пароль Postgres в $SB_ENV_FILE${NC}"
                break
            fi
        fi
    done
fi

# Если пароль найден — собираем DATABASE_URL
if [ -n "$SUPABASE_DB_PASS" ]; then
    # Пробуем localhost сначала, затем 127.0.0.1 (pooler обычно на порте 5432)
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
            echo -e "${GREEN}PostgreSQL доступен на ${PG_HOST}:5432${NC}"
            break
        fi
    done
fi

# --- Попытка 2: спросить пользователя (всегда опционально) ---
if [ -z "$DATABASE_URL_VALUE" ]; then
    echo -e "${YELLOW}Не удалось автоматически определить DATABASE_URL.${NC}"
    echo -e "${YELLOW}Введите DATABASE_URL вручную (оставьте пустым чтобы пропустить):${NC}"
    echo -e "  Пример self-hosted: postgresql://postgres:password@localhost:5432/postgres"
    echo -e "  Пример supabase.co:  postgresql://postgres.xxx:pass@aws-region.pooler.supabase.com:6543/postgres"
    read -p "DATABASE_URL: " DATABASE_URL_VALUE
fi

# Записываем в .env
if [ -n "$DATABASE_URL_VALUE" ]; then
    # Удаляем старую строку если есть, добавляем новую
    grep -qE '^DATABASE_URL=' .env 2>/dev/null && sed -i "s|^DATABASE_URL=.*|DATABASE_URL=$DATABASE_URL_VALUE|" .env \
        || echo "DATABASE_URL=$DATABASE_URL_VALUE" >> .env
    echo -e "${GREEN}DATABASE_URL записан в .env (publisher будет хранить лимиты в Supabase)${NC}"
else
    echo -e "${YELLOW}Предупреждение: DATABASE_URL не задан. Сервис будет использовать in-memory счётчик.${NC}"
fi

echo -e "\n${YELLOW}[6/6] Создание системной службы systemd...${NC}"
SERVICE_FILE="/etc/systemd/system/dzen-publisher.service"

cat <<EOT > "$SERVICE_FILE"
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

echo -e "${GREEN}Служба systemd создана: $SERVICE_FILE${NC}"
systemctl daemon-reload
systemctl enable dzen-publisher
systemctl restart dzen-publisher

# Проверка статуса службы
sleep 2
if systemctl is-active --quiet dzen-publisher; then
    echo -e "\n${GREEN}${BOLD}✔ Установка успешно завершена! Service is RUNNING.${NC}"
else
    echo -e "\n${RED}${BOLD}✘ Ошибка: Служба не запустилась. Проверьте логи: journalctl -u dzen-publisher -n 50${NC}"
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
