#!/usr/bin/env bash
# ============================================================
# deploy.sh — установка TikTok Scheduler на Ubuntu-сервер
# Использование: bash deploy.sh
#
# Требования: Ubuntu 20.04+, Python 3.10+, ffmpeg, curl
# НЕ трогает nginx/apache и существующие сайты.
# ============================================================

set -e  # остановить при ошибке

# ── Настройки ────────────────────────────────────────────────
APP_DIR="/opt/tiktok-scheduler"
APP_USER="tiktok"          # Отдельный пользователь для безопасности
SERVICE_NAME="tiktok-scheduler"
PYTHON_BIN="python3"
# ─────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step()    { echo -e "\n${YELLOW}══ $1 ══${NC}"; }

# ── Проверка прав ─────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
  error "Запустите как root: sudo bash deploy.sh"
fi

# ── 1. Системные зависимости ──────────────────────────────────
step "1. Установка системных пакетов"
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv python3-dev \
    ffmpeg \
    curl wget git \
    build-essential \
    > /dev/null
info "Системные пакеты установлены"

# ── 2. Создание пользователя ──────────────────────────────────
step "2. Создание системного пользователя $APP_USER"
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --shell /bin/bash --home "$APP_DIR" --create-home "$APP_USER"
    info "Пользователь $APP_USER создан"
else
    warn "Пользователь $APP_USER уже существует"
fi

# ── 3. Копирование файлов ─────────────────────────────────────
step "3. Копирование файлов приложения в $APP_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Создаём папку если не существует
mkdir -p "$APP_DIR"

# Копируем python-файлы (не перезаписываем .env если уже есть)
rsync -av --exclude='.venv' --exclude='.venv-1' --exclude='__pycache__' \
    --exclude='logs/' --exclude='output/' \
    "$SCRIPT_DIR/" "$APP_DIR/" \
    > /dev/null

# .env копируем только если ещё нет
if [ ! -f "$APP_DIR/.env" ]; then
    if [ -f "$SCRIPT_DIR/.env" ]; then
        cp "$SCRIPT_DIR/.env" "$APP_DIR/.env"
        info ".env скопирован"
    else
        warn ".env не найден! Создайте $APP_DIR/.env вручную."
    fi
else
    warn ".env уже существует — не перезаписываем"
fi

# cookies.txt
if [ -f "$SCRIPT_DIR/cookies.txt" ]; then
    cp "$SCRIPT_DIR/cookies.txt" "$APP_DIR/cookies.txt"
    info "cookies.txt скопирован"
fi

# Создаём рабочие папки
mkdir -p "$APP_DIR/logs/audio" "$APP_DIR/logs/background" \
         "$APP_DIR/logs/temp" "$APP_DIR/output"

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
info "Файлы скопированы в $APP_DIR"

# ── 4. Python venv + зависимости ─────────────────────────────
step "4. Создание Python venv и установка зависимостей"
sudo -u "$APP_USER" bash -c "
    cd $APP_DIR
    $PYTHON_BIN -m venv venv
    venv/bin/pip install --upgrade pip -q
    venv/bin/pip install -r requirements.txt -q
    venv/bin/pip install 'yt-dlp[default]' -q
"
info "Python-зависимости установлены"

# ── 5. Установка deno (JS runtime для yt-dlp n-challenge) ─────
step "5. Установка deno"
DENO_DIR="/home/$APP_USER/.deno/bin"
DENO_EXE="$DENO_DIR/deno"

if [ ! -f "$DENO_EXE" ]; then
    sudo -u "$APP_USER" bash -c "
        mkdir -p $DENO_DIR
        cd /tmp
        curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o deno.zip
        unzip -o deno.zip -d $DENO_DIR
        rm deno.zip
        chmod +x $DENO_EXE
    "
    info "deno установлен: $DENO_EXE"
else
    warn "deno уже установлен"
fi

# ── 6. Установка yt-dlp (последняя версия) ────────────────────
step "6. yt-dlp"
sudo -u "$APP_USER" bash -c "$APP_DIR/venv/bin/pip install -U yt-dlp 'yt-dlp[default]' -q"
info "yt-dlp обновлён"

# ── 7. Создание systemd service ───────────────────────────────
step "7. Создание systemd сервиса $SERVICE_NAME"

DENO_BIN_PATH="/home/$APP_USER/.deno/bin"

cat > "/etc/systemd/system/$SERVICE_NAME.service" << EOF
[Unit]
Description=TikTok Content Scheduler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin:$DENO_BIN_PATH"
Environment="PYTHONUNBUFFERED=1"
Environment="PYTHONIOENCODING=utf-8"
ExecStart=$APP_DIR/venv/bin/python scheduler.py
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

# Лимиты ресурсов (не мешаем основному сайту)
MemoryMax=2G
CPUQuota=80%
Nice=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
info "Сервис $SERVICE_NAME создан и включён в автозапуск"

# ── 8. Запуск ─────────────────────────────────────────────────
step "8. Запуск сервиса"
systemctl restart "$SERVICE_NAME"
sleep 3

if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Сервис запущен успешно!"
else
    warn "Сервис не запустился. Проверьте: journalctl -u $SERVICE_NAME -n 50"
fi

# ── Итог ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        Деплой завершён успешно!          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo "  Приложение:  $APP_DIR"
echo "  Логи:        journalctl -u $SERVICE_NAME -f"
echo "  Статус:      systemctl status $SERVICE_NAME"
echo "  Стоп:        systemctl stop $SERVICE_NAME"
echo "  Рестарт:     systemctl restart $SERVICE_NAME"
echo ""
echo -e "${YELLOW}Не забудьте:${NC}"
echo "  1. Проверьте $APP_DIR/.env (API ключи, токены, прокси)"
echo "  2. Добавьте категории фонов через Telegram-бота: /bg"
echo "  3. Проверьте cookies.txt (если YouTube требует аутентификацию)"
