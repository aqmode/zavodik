"""
Telegram-бот для управления TikTok Scheduler'ом.

Команды:
  /start      — главное меню
  /status     — статус
  /settopic   — сменить тему поиска
  /channels   — управление YouTube-каналами
  /bg         — управление категориями фоновых видео

Структура категорий фонов:
  categories.json  → [{"name": "glass_cleaning"}, ...]
  Папки:           → categories/<name>/  (туда боту кидают mp4-файлы)
"""

import os
import re
import json
import time
import threading
import logging
from dotenv import load_dotenv, dotenv_values

load_dotenv()

log = logging.getLogger("bot_listener")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


# ─── пути ────────────────────────────────────────────────────────────────────

def _root() -> str:
    return os.path.dirname(os.path.abspath(__file__))

def _env_path() -> str:
    return os.path.join(_root(), ".env")

def _categories_path() -> str:
    return os.path.join(_root(), "categories.json")


# ─── categories.json ─────────────────────────────────────────────────────────

def load_categories() -> list:
    path = _categories_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_categories(cats: list):
    with open(_categories_path(), "w", encoding="utf-8") as f:
        json.dump(cats, f, ensure_ascii=False, indent=2)


def get_category(name: str):
    for c in load_categories():
        if c["name"] == name:
            return c
    return None


def _cat_dir(name: str) -> str:
    """Папка категории: categories/<name>/"""
    return os.path.join(_root(), "categories", name)


def add_category(name: str):
    cats = load_categories()
    if not any(c["name"] == name for c in cats):
        cats.append({"name": name})
        save_categories(cats)
    os.makedirs(_cat_dir(name), exist_ok=True)


def remove_category(name: str):
    save_categories([c for c in load_categories() if c["name"] != name])
    # папку не удаляем — видео остаются на диске


def add_video_to_category(name: str, url: str) -> bool:
    """Устарело — оставлено для совместимости. Видео теперь кидают файлом."""
    return get_category(name) is not None


def remove_video_from_category(name: str, idx: int):
    """Удаляет видео-файл из папки категории по индексу."""
    cat_dir = _cat_dir(name)
    if not os.path.isdir(cat_dir):
        return
    videos = sorted([
        f for f in os.listdir(cat_dir)
        if f.lower().endswith((".mp4", ".webm", ".mkv", ".mov"))
    ])
    if 0 <= idx < len(videos):
        try:
            os.remove(os.path.join(cat_dir, videos[idx]))
        except OSError:
            pass


def list_videos_in_category(name: str) -> list[str]:
    """Список имён файлов в папке категории."""
    cat_dir = _cat_dir(name)
    if not os.path.isdir(cat_dir):
        return []
    return sorted([
        f for f in os.listdir(cat_dir)
        if f.lower().endswith((".mp4", ".webm", ".mkv", ".mov"))
    ])


# ─── .env helpers ─────────────────────────────────────────────────────────────

def _get_current_topic() -> str:
    return dotenv_values(_env_path()).get("CONTENT_TOPIC", os.getenv("CONTENT_TOPIC", "—"))


def _update_env_key(key: str, value: str):
    env_path = _env_path()
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()
    if re.search(rf"^{key}\s*=", content, re.MULTILINE):
        content = re.sub(rf"^({key}\s*=).*$", rf"\g<1>{value}", content, flags=re.MULTILINE)
    else:
        content += f"\n{key}={value}\n"
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.environ[key] = value
    log.info(f"ENV updated: {key}={value}")


def _get_channels() -> list:
    raw = dotenv_values(_env_path()).get("YOUTUBE_CHANNELS",
                                        os.getenv("YOUTUBE_CHANNELS", ""))
    return [c.strip() for c in raw.split(",") if c.strip()]


def _set_channels(channels: list):
    _update_env_key("YOUTUBE_CHANNELS", ",".join(channels))


# ─── Telegram API ─────────────────────────────────────────────────────────────

def _api(method: str, data: dict = None, timeout: float = 30.0):
    import httpx
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    proxy = os.getenv("PROXY") or None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        kwargs = {"timeout": timeout}
        if proxy:
            kwargs["proxy"] = proxy
        with httpx.Client(**kwargs) as client:
            resp = client.post(url, json=data or {})
        result = resp.json()
        if not result.get("ok"):
            log.warning(f"API /{method}: {result.get('description','?')}")
            return None
        return result.get("result")
    except Exception as e:
        log.error(f"API ошибка ({method}): {e}")
        return None


def _send(chat_id: str, text: str, markup: dict = None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if markup:
        data["reply_markup"] = markup
    _api("sendMessage", data)


def _edit(chat_id: str, message_id: int, text: str, markup: dict = None):
    data = {"chat_id": chat_id, "message_id": message_id,
            "text": text, "parse_mode": "HTML"}
    if markup:
        data["reply_markup"] = markup
    _api("editMessageText", data)


def _answer(callback_id: str):
    _api("answerCallbackQuery", {"callback_query_id": callback_id})


# ─── состояния диалогов ───────────────────────────────────────────────────────
# chat_id → {"state": str, "data": dict}

_states: dict = {}


# ─── меню ─────────────────────────────────────────────────────────────────────

def _kb(*rows):
    """Shortcut: _kb(["text","cb"], ["text2","cb2"]) → inline_keyboard"""
    return {"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in row]
                                 for row in rows]}


def _menu_main(chat_id: str, message_id: int = None):
    topic = _get_current_topic()
    text = (
        "🎬 <b>TikTok Content Generator</b>\n\n"
        f"📝 Тема: <b>{topic}</b>\n\n"
        "Выберите раздел:"
    )
    markup = _kb(
        [("🎯 Сменить тему", "set_topic")],
        [("📺 YouTube-каналы", "menu_channels"), ("🖼 Фоны", "menu_bg")],
        [("🍪 Cookies", "menu_cookies"), ("📊 Статус", "status")],
    )
    if message_id:
        _edit(chat_id, message_id, text, markup)
    else:
        _send(chat_id, text, markup)


def _menu_cookies(chat_id: str, message_id: int = None):
    cookies_file = os.path.join(_root(), "cookies.txt")
    exists = os.path.exists(cookies_file)
    size = os.path.getsize(cookies_file) if exists else 0
    mtime = ""
    if exists:
        import datetime
        mtime = datetime.datetime.fromtimestamp(
            os.path.getmtime(cookies_file)
        ).strftime("%Y-%m-%d %H:%M")

    text = (
        "🍪 <b>Cookies</b>\n\n"
        + (f"📄 Файл: <code>cookies.txt</code>\n"
           f"📦 Размер: {size} байт\n"
           f"🕐 Изменён: {mtime}" if exists else "⚠️ <b>cookies.txt не найден!</b>")
        + "\n\n"
        "Для обновления:\n"
        "1. Откройте YouTube в браузере (войдите в аккаунт)\n"
        "2. Расширение <b>Get cookies.txt LOCALLY</b>\n"
        "3. Экспортируйте cookies → скачается файл\n"
        "4. Нажмите «📎 Обновить cookies» и отправьте файл"
    )
    markup = _kb(
        [("� Обновить cookies", "cookies_paste")],
        [("🔙 Назад", "menu_main")],
    )
    if message_id:
        _edit(chat_id, message_id, text, markup)
    else:
        _send(chat_id, text, markup)


def _menu_channels(chat_id: str, message_id: int = None):
    channels = _get_channels()
    lines = ["📺 <b>YouTube-каналы</b>\n"]
    for i, ch in enumerate(channels, 1):
        name = ch.split("@")[-1] if "@" in ch else ch[-30:]
        lines.append(f"{i}. @{name}")
    if not channels:
        lines.append("<i>Каналов нет — добавьте хотя бы один</i>")

    btns = []
    for i, ch in enumerate(channels):
        name = ch.split("@")[-1] if "@" in ch else ch[-20:]
        btns.append([{"text": f"❌ Удалить @{name}", "callback_data": f"ch_del_{i}"}])
    btns.append([{"text": "➕ Добавить канал", "callback_data": "ch_add"}])
    btns.append([{"text": "🔙 Назад", "callback_data": "menu_main"}])
    markup = {"inline_keyboard": btns}

    if message_id:
        _edit(chat_id, message_id, "\n".join(lines), markup)
    else:
        _send(chat_id, "\n".join(lines), markup)


def _menu_bg(chat_id: str, message_id: int = None):
    cats = load_categories()
    lines = ["🖼 <b>Категории фоновых видео</b>\n"]
    for c in cats:
        cnt = len(list_videos_in_category(c["name"]))
        lines.append(f"• <b>{c['name']}</b> — {cnt} видео")
    if not cats:
        lines.append("<i>Категорий нет — создайте хотя бы одну</i>")

    btns = []
    for c in cats:
        cnt = len(list_videos_in_category(c["name"]))
        btns.append([{"text": f"📂 {c['name']} ({cnt})",
                      "callback_data": f"bg_open_{c['name']}"}])
    btns.append([{"text": "➕ Новая категория", "callback_data": "bg_new"}])
    btns.append([{"text": "🔙 Назад", "callback_data": "menu_main"}])
    markup = {"inline_keyboard": btns}

    if message_id:
        _edit(chat_id, message_id, "\n".join(lines), markup)
    else:
        _send(chat_id, "\n".join(lines), markup)


def _menu_bg_cat(chat_id: str, cat_name: str, message_id: int = None):
    cat = get_category(cat_name)
    if not cat:
        _send(chat_id, "❌ Категория не найдена.")
        return

    videos = list_videos_in_category(cat_name)
    lines = [f"📂 <b>{cat_name}</b>  ({len(videos)} видео)\n"]
    for i, fname in enumerate(videos, 1):
        lines.append(f"{i}. <code>{fname}</code>")
    if not videos:
        lines.append("<i>Видео нет — отправьте .mp4 файл(ы) в этот чат</i>\n"
                     "<i>Сначала нажмите «📥 Выбрать эту категорию»</i>")

    btns = []
    for i, fname in enumerate(videos):
        short = fname[:20] + "…" if len(fname) > 20 else fname
        btns.append([{"text": f"❌ {short}", "callback_data": f"bgv_del_{cat_name}|{i}"}])
    btns.append([{"text": "📥 Слать видео сюда", "callback_data": f"bg_select_{cat_name}"}])
    btns.append([{"text": "🗑 Удалить категорию", "callback_data": f"bg_del_cat_{cat_name}"}])
    btns.append([{"text": "🔙 К категориям", "callback_data": "menu_bg"}])
    markup = {"inline_keyboard": btns}

    if message_id:
        _edit(chat_id, message_id, "\n".join(lines), markup)
    else:
        _send(chat_id, "\n".join(lines), markup)


def _menu_status(chat_id: str, message_id: int = None):
    topic = _get_current_topic()
    cats = load_categories()
    channels = _get_channels()
    logs_dir = os.getenv("DOWNLOAD_DIR", "logs")
    used_file = os.path.join(_root(), logs_dir, "used_videos.txt")
    try:
        with open(used_file, encoding="utf-8") as f:
            used_count = sum(1 for line in f if line.strip())
    except Exception:
        used_count = 0
    bg_count = sum(len(list_videos_in_category(c["name"])) for c in cats)
    text = (
        "📊 <b>Статус</b>\n\n"
        f"📝 Тема: <b>{topic}</b>\n"
        f"📺 Каналов: <b>{len(channels)}</b>\n"
        f"🖼 Категорий: <b>{len(cats)}</b> ({bg_count} фон-видео)\n"
        f"📋 Использовано видео: <b>{used_count}</b>\n"
    )
    markup = _kb([("🔙 Меню", "menu_main")])
    if message_id:
        _edit(chat_id, message_id, text, markup)
    else:
        _send(chat_id, text, markup)


def _vid_id(url: str):
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


# ─── обработка сообщений ──────────────────────────────────────────────────────

def _handle_message(message: dict):
    chat_id = str(message["chat"]["id"])
    text = message.get("text", "").strip()
    st = _states.get(chat_id, {})
    state = st.get("state", "")

    # ── приём видео-файла в категорию ──────────────────────────────
    video_obj = message.get("video") or message.get("document")

    # ── приём cookies.txt как файла ──────────────────────────────
    if video_obj and state == "wait_cookies_file":
        _states.pop(chat_id, None)
        file_id = video_obj.get("file_id", "")
        file_info = _api("getFile", {"file_id": file_id})
        if file_info and file_info.get("file_path"):
            import httpx
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            proxy = os.getenv("PROXY") or None
            dl_url = f"https://api.telegram.org/file/bot{token}/{file_info['file_path']}"
            try:
                kwargs = {"timeout": 60}
                if proxy:
                    kwargs["proxy"] = proxy
                with httpx.Client(**kwargs) as client:
                    resp = client.get(dl_url)
                cookie_text = resp.text
                cookies_file = os.path.join(_root(), "cookies.txt")
                with open(cookies_file, "w", encoding="utf-8") as f:
                    f.write(cookie_text)
                lines_count = sum(1 for l in cookie_text.splitlines()
                                  if l.strip() and not l.startswith("#"))
                _send(chat_id,
                      f"✅ <b>cookies.txt обновлён!</b>\n"
                      f"📦 Записано {lines_count} записей cookie.\n"
                      f"📄 Размер: {len(cookie_text)} байт")
            except Exception as e:
                _send(chat_id, f"❌ Ошибка: {e}")
        else:
            _send(chat_id, "❌ Не удалось получить файл.")
        _menu_cookies(chat_id)
        return

    if video_obj and state == "wait_bg_video_file":
        cat_name = st.get("data", {}).get("cat", "")
        _states.pop(chat_id, None)

        file_id = video_obj.get("file_id", "")
        file_size = video_obj.get("file_size", 0)
        orig_name = video_obj.get("file_name", f"{file_id}.mp4")
        if not orig_name.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
            orig_name += ".mp4"

        # Telegram Bot API позволяет скачивать файлы только до 20 МБ через getFile
        MAX_DL = 20 * 1024 * 1024
        if file_size and file_size > MAX_DL:
            size_mb = file_size / 1024 / 1024
            _send(chat_id,
                  f"❌ Файл слишком большой ({size_mb:.0f} МБ).\n\n"
                  f"Telegram Bot API позволяет скачивать файлы только до 20 МБ.\n"
                  f"Сожмите видео или обрежьте до нужного размера и отправьте снова.")
            _states[chat_id] = {"state": "wait_bg_video_file", "data": {"cat": cat_name}}
            return

        cat_dir = _cat_dir(cat_name)
        os.makedirs(cat_dir, exist_ok=True)
        save_path = os.path.join(cat_dir, orig_name)

        file_info = _api("getFile", {"file_id": file_id})
        if file_info and file_info.get("file_path"):
            import httpx
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            proxy = os.getenv("PROXY") or None
            dl_url = f"https://api.telegram.org/file/bot{token}/{file_info['file_path']}"
            try:
                kwargs = {"timeout": 300}
                if proxy:
                    kwargs["proxy"] = proxy
                with httpx.Client(**kwargs) as client:
                    resp = client.get(dl_url)
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                size_mb = os.path.getsize(save_path) / 1024 / 1024
                _send(chat_id,
                      f"✅ Видео сохранено в <b>{cat_name}</b>\n"
                      f"📁 <code>{orig_name}</code> ({size_mb:.1f} МБ)\n\n"
                      f"Можете отправить ещё видео или нажмите /bg")
            except Exception as e:
                _send(chat_id, f"❌ Ошибка сохранения: {e}")
        else:
            _send(chat_id,
                  "❌ Не удалось получить ссылку на файл от Telegram.\n"
                  "Попробуйте отправить файл ещё раз.")
        # Остаёмся в состоянии — можно слать ещё видео
        _states[chat_id] = {"state": "wait_bg_video_file", "data": {"cat": cat_name}}
        return

    # ── диалоговые состояния ──
    if state == "wait_topic":
        _states.pop(chat_id, None)
        if text and not text.startswith("/"):
            _update_env_key("CONTENT_TOPIC", text)
            _send(chat_id, f"✅ Тема обновлена: <b>{text}</b>")
            _menu_main(chat_id)
        else:
            _send(chat_id, "⚠ Отменено.")
        return

    if state == "wait_channel_add":
        _states.pop(chat_id, None)
        if text and not text.startswith("/"):
            url = text.strip().rstrip("/")
            channels = _get_channels()
            if url not in channels:
                channels.append(url)
                _set_channels(channels)
                _send(chat_id, f"✅ Канал добавлен: <code>{url}</code>")
            else:
                _send(chat_id, "⚠ Этот канал уже есть.")
            _menu_channels(chat_id)
        else:
            _send(chat_id, "⚠ Отменено.")
        return

    if state == "wait_bg_cat_name":
        _states.pop(chat_id, None)
        if text and not text.startswith("/"):
            name = re.sub(r"[^\w\-]", "_", text.strip())
            add_category(name)
            _send(chat_id, f"✅ Категория <b>{name}</b> создана.")
            _menu_bg(chat_id)
        else:
            _send(chat_id, "⚠ Отменено.")
        return

    if state == "wait_cookies_file":
        _states.pop(chat_id, None)
        # Если пользователь прислал текст вместо файла — тоже принимаем
        if text and not text.startswith("/"):
            cookies_file = os.path.join(_root(), "cookies.txt")
            try:
                with open(cookies_file, "w", encoding="utf-8") as f:
                    f.write(text)
                lines_count = sum(1 for l in text.splitlines()
                                  if l.strip() and not l.startswith("#"))
                _send(chat_id,
                      f"✅ <b>cookies.txt обновлён!</b>\n"
                      f"📦 Записано {lines_count} записей cookie.\n"
                      f"📄 Размер: {len(text)} байт")
            except Exception as e:
                _send(chat_id, f"❌ Ошибка записи: {e}")
        else:
            _send(chat_id, "⚠ Отменено.")
        _menu_cookies(chat_id)
        return

    # ── команды ──
    cmd = text.split()[0].lower() if text else ""
    if cmd in ("/start", "/menu"):
        _menu_main(chat_id)
    elif cmd == "/settopic":
        _states[chat_id] = {"state": "wait_topic"}
        _send(chat_id, "✏️ Введите новую тему:")
    elif cmd == "/status":
        _menu_status(chat_id)
    elif cmd == "/channels":
        _menu_channels(chat_id)
    elif cmd == "/bg":
        _menu_bg(chat_id)
    else:
        _menu_main(chat_id)


def _handle_callback(callback: dict):
    chat_id = str(callback["message"]["chat"]["id"])
    message_id = callback["message"]["message_id"]
    data = callback.get("data", "")
    _answer(callback["id"])

    if data == "menu_main":
        _menu_main(chat_id, message_id)

    elif data == "status":
        _menu_status(chat_id, message_id)

    elif data == "menu_cookies":
        _menu_cookies(chat_id, message_id)

    elif data == "cookies_paste":
        _states[chat_id] = {"state": "wait_cookies_file"}
        _edit(chat_id, message_id,
              "🍪 <b>Обновление cookies</b>\n\n"
              "Отправьте файл <b>cookies.txt</b> в этот чат.\n\n"
              "Как получить:\n"
              "1. Откройте YouTube в браузере (войдите в аккаунт)\n"
              "2. Расширение <b>Get cookies.txt LOCALLY</b>\n"
              "3. Экспортируйте → получите файл cookies.txt\n"
              "4. Отправьте этот файл сюда\n\n"
              "Или вставьте текст cookies прямо в чат.")

    elif data == "set_topic":
        _states[chat_id] = {"state": "wait_topic"}
        _edit(chat_id, message_id, "✏️ Введите новую тему (например: <i>Комплексы парней</i>):")

    # ── каналы ──
    elif data == "menu_channels":
        _menu_channels(chat_id, message_id)

    elif data == "ch_add":
        _states[chat_id] = {"state": "wait_channel_add"}
        _edit(chat_id, message_id,
              "➕ Введите URL YouTube-канала:\n"
              "Пример: <code>https://www.youtube.com/@channelname</code>")

    elif data.startswith("ch_del_"):
        idx = int(data.split("_")[-1])
        channels = _get_channels()
        if 0 <= idx < len(channels):
            removed = channels.pop(idx)
            _set_channels(channels)
            _send(chat_id, f"✅ Канал удалён: <code>{removed}</code>")
        _menu_channels(chat_id, message_id)

    # ── категории фонов ──
    elif data == "menu_bg":
        _menu_bg(chat_id, message_id)

    elif data == "bg_new":
        _states[chat_id] = {"state": "wait_bg_cat_name"}
        _edit(chat_id, message_id,
              "➕ Введите название новой категории:\n"
              "(только буквы/цифры/дефис, пробелы → _)\n"
              "Пример: <code>glass_cleaning</code>")

    elif data.startswith("bg_open_"):
        cat_name = data[len("bg_open_"):]
        _menu_bg_cat(chat_id, cat_name, message_id)

    elif data.startswith("bg_del_cat_"):
        cat_name = data[len("bg_del_cat_"):]
        remove_category(cat_name)
        _send(chat_id, f"🗑 Категория <b>{cat_name}</b> удалена.")
        _menu_bg(chat_id, message_id)

    elif data.startswith("bg_select_"):
        cat_name = data[len("bg_select_"):]
        _states[chat_id] = {"state": "wait_bg_video_file", "data": {"cat": cat_name}}
        _edit(chat_id, message_id,
              f"📥 <b>{cat_name}</b>\n\n"
              f"Отправьте видео-файл(ы) (.mp4) прямо в этот чат.\n"
              f"Можно отправить несколько подряд.\n\n"
              f"Когда закончите — нажмите /bg")

    elif data.startswith("bgv_del_"):
        # формат: bgv_del_<cat_name>|<idx>
        payload = data[len("bgv_del_"):]
        if "|" in payload:
            cat_name, idx_str = payload.rsplit("|", 1)
            remove_video_from_category(cat_name, int(idx_str))
            _send(chat_id, f"✅ Видео удалено из <b>{cat_name}</b>.")
            _menu_bg_cat(chat_id, cat_name, message_id)


# ─── polling ──────────────────────────────────────────────────────────────────

_last_update_id = 0


def _poll_loop():
    global _last_update_id
    log.info("Bot polling запущен.")

    while True:
        try:
            result = _api("getUpdates", {
                "offset": _last_update_id + 1,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=40.0)

            if not result:
                time.sleep(2)
                continue

            for update in result:
                _last_update_id = update["update_id"]
                try:
                    if "message" in update:
                        _handle_message(update["message"])
                    elif "callback_query" in update:
                        _handle_callback(update["callback_query"])
                except Exception as e:
                    log.error(f"Ошибка update: {e}", exc_info=True)

        except Exception as e:
            log.error(f"Polling ошибка: {e}")
            time.sleep(5)


def start_bot_thread():
    """Запускает polling в daemon-потоке."""
    t = threading.Thread(target=_poll_loop, daemon=True, name="TelegramBot")
    t.start()
    return t


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    log.info("Запуск бота...")
    _poll_loop()
