# 🚀 Деплой на Ubuntu-сервер

## Требования

- Ubuntu 20.04+ (или Debian 11+)
- Python 3.10+
- Минимум 2 GB RAM (faster-whisper на CPU)
- ffmpeg (устанавливается автоматически)
- Порты наружу не нужны — только исходящие соединения

> **Безопасно для существующих сайтов:** сервис работает от отдельного пользователя `tiktok`, не занимает 80/443 порты, не затрагивает nginx/apache.

---

## Шаг 1 — Скопировать файлы на сервер

```bash
# С локальной Windows-машины (в PowerShell):
scp -r "C:\Users\Unison\Desktop\SCRIPTI\pythonTekTak\" user@your-server-ip:/tmp/tiktok-app

# Или через git (создайте приватный репо):
# git push origin main
# git clone <repo> /tmp/tiktok-app
```

---

## Шаг 2 — Запустить деплой

```bash
ssh user@your-server-ip
cd /tmp/tiktok-app
sudo bash deploy.sh
```

Скрипт сделает всё автоматически:
- Установит `ffmpeg`, `python3-venv`
- Создаст пользователя `tiktok`
- Скопирует приложение в `/opt/tiktok-scheduler/`
- Создаст Python venv и установит зависимости
- Установит `deno` (нужен для YouTube n-challenge)
- Создаст и запустит systemd-сервис `tiktok-scheduler`

---

## Шаг 3 — Проверить .env на сервере

```bash
sudo nano /opt/tiktok-scheduler/.env
```

Убедитесь что заполнены:
```
GROQ_API_KEY=...
YOUTUBE_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
PROXY=http://user:pass@host:port       # прокси для Groq/Telegram
YTDLP_PROXY=http://user:pass@host:port # прокси для YouTube
CONTENT_TOPIC=Комплексы парней
```

---

## Шаг 4 — Добавить категории фонов через Telegram-бот

1. Откройте вашего бота в Telegram
2. `/start` → **🖼 Фоны** → **➕ Новая категория**
3. Введите название (например: `glass_cleaning`)
4. Откройте категорию → **➕ Добавить видео** → вставьте YouTube-URL
5. При следующем запуске цикла видео скачается автоматически

---

## Управление сервисом

```bash
# Статус
systemctl status tiktok-scheduler

# Логи в реальном времени
journalctl -u tiktok-scheduler -f

# Последние 100 строк логов
journalctl -u tiktok-scheduler -n 100

# Рестарт (после изменений .env или кода)
systemctl restart tiktok-scheduler

# Остановить
systemctl stop tiktok-scheduler

# Запустить снова
systemctl start tiktok-scheduler
```

---

## Обновление кода

```bash
# Скопировать новые .py файлы (не трогая .env и данные)
sudo rsync -av --exclude='.env' --exclude='logs/' --exclude='output/' \
    /tmp/tiktok-app/ /opt/tiktok-scheduler/

# Обновить зависимости если нужно
sudo -u tiktok /opt/tiktok-scheduler/venv/bin/pip install -r /opt/tiktok-scheduler/requirements.txt

# Перезапустить
sudo systemctl restart tiktok-scheduler
```

---

## Обновление cookies.txt (если YouTube заблокировал)

```bash
# Скопировать новый cookies.txt на сервер
scp cookies.txt user@your-server:/opt/tiktok-scheduler/cookies.txt
sudo chown tiktok:tiktok /opt/tiktok-scheduler/cookies.txt

# Рестарт не нужен — файл читается при каждом скачивании
```

---

## Структура Telegram-бота

```
/start или /menu     — главное меню
/status              — текущая тема, каналы, статистика
/settopic            — сменить тему поиска
/channels            — добавить/удалить YouTube-каналы
/bg                  — управление категориями фоновых видео
```

### Управление категориями фонов

```
🖼 Фоны
├── 📂 glass_cleaning (3 видео)   ← открыть категорию
│   ├── ❌ dQw4w9W...              ← удалить видео
│   ├── ➕ Добавить видео          ← вставить YouTube URL
│   └── 🗑 Удалить категорию
└── ➕ Новая категория             ← создать новую
```

Добавленные видео хранятся в `categories.json` и скачиваются в `logs/background/<category>/` при следующем цикле.

---

## Структура файлов на сервере

```
/opt/tiktok-scheduler/
├── scheduler.py        ← главный процесс
├── bot_listener.py     ← Telegram-бот
├── downloader.py       ← скачивание аудио
├── video_processor.py  ← субтитры + сборка клипов
├── ai_module.py        ← Groq AI
├── telegram_bot.py     ← отправка в Telegram
├── youtube_search.py   ← поиск видео
├── .env                ← настройки (не трогать вручную)
├── categories.json     ← категории фонов (управляется ботом)
├── cookies.txt         ← YouTube cookies
├── eitai.otf           ← шрифт субтитров
├── used_videos.txt     ← уже обработанные видео
├── venv/               ← Python виртуальное окружение
├── logs/
│   ├── audio/          ← временное аудио
│   ├── background/     ← фоновые видео по категориям
│   ├── temp/           ← временные чанки
│   └── scheduler.log   ← лог работы
└── output/             ← готовые клипы (удаляются после отправки)
```

---

## FAQ

**Q: Не скачивается аудио (bot detection)?**  
A: Обновите `cookies.txt` — экспортируйте заново из браузера расширением "Get cookies.txt LOCALLY"

**Q: Сервис падает каждые 5 минут?**  
A: Смотрите логи: `journalctl -u tiktok-scheduler -n 50`  
Обычно причина: неверный API ключ в `.env`

**Q: Как изменить частоту (не 24 часа)?**  
A: В `.env`: `CYCLE_INTERVAL_HOURS=12` — или отредактируйте `scheduler.py`

**Q: Можно ли запустить первый цикл сразу без ожидания?**  
A: `systemctl restart tiktok-scheduler` — цикл запускается немедленно при старте
