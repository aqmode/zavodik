"""
Scheduler — главный процесс для деплоя на сервер.

Логика:
- Каждый цикл: скачать и обработать VIDEOS_PER_CYCLE видео (по 3 клипа каждое = 6 клипов)
- Отправить все клипы в Telegram по мере готовности
- После отправки последнего клипа — ждать 24 часа
- Тема меняется только через Telegram-бота (/settopic)
- Бот запускается в фоновом потоке и всё время слушает команды

Запуск:
    python scheduler.py

Для остановки: Ctrl+C или kill процесса.
"""

import os
import sys
import time
import threading
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logs_dir = os.getenv("DOWNLOAD_DIR", "logs")
os.makedirs(logs_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(logs_dir, "scheduler.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scheduler")

# Глушим спам httpx/httpcore и googleapiclient
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

CYCLE_INTERVAL_SEC  = 24 * 60 * 60
VIDEOS_PER_CYCLE    = int(os.getenv("VIDEOS_PER_CYCLE", "2"))
CLIPS_PER_VIDEO     = int(os.getenv("CLIPS_PER_VIDEO", "3"))
DELAY_BETWEEN_SENDS = float(os.getenv("DELAY_BETWEEN_SENDS", "3"))


# ─── вспомогательные утилиты ────────────────────────────────────────────────

def _reload_env():
    """Перечитывает .env (нужно для обновления темы ботом в реальном времени)."""
    from dotenv import dotenv_values
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    vals = dotenv_values(env_path)
    for k, v in vals.items():
        os.environ[k] = v or ""


def _log_step(msg: str):
    log.info(msg)


# ─── шаги пайплайна ─────────────────────────────────────────────────────────

def _get_bg_by_category():
    """
    Возвращает dict {category: [список путей к видео]}.
    Читает файлы из categories/<name>/ (туда пользователь кидает mp4 через бота).
    """
    from bot_listener import load_categories, list_videos_in_category, _cat_dir

    result = {}
    cats = load_categories()
    for cat in cats:
        name = cat["name"]
        cat_dir = _cat_dir(name)
        if os.path.isdir(cat_dir):
            videos = sorted([
                os.path.join(cat_dir, f)
                for f in list_videos_in_category(name)
            ])
            if videos:
                result[name] = videos

    return result


def _notify_cookies_expired():
    """Отправляет уведомление в Telegram что куки протухли."""
    try:
        from telegram_bot import send_notification
        send_notification(
            "⚠️ <b>Куки YouTube протухли!</b>\n\n"
            "YouTube требует авторизацию — скачивание аудио невозможно.\n\n"
            "Обновите cookies.txt через бота:\n"
            "/start → 🍪 Cookies → 📋 Вставить текст cookies"
        )
    except Exception as e:
        log.error(f"Не удалось отправить уведомление о куках: {e}")


def _process_one_video(video: dict, video_index: int, used_bg: set = None) -> tuple[list, set]:
    """
    Обрабатывает одно выбранное видео:
      1. Скачивание аудио
      2. Нарезка + субтитры + сборка (3 клипа)
    Возвращает (список путей к готовым клипам, обновлённый set использованных фонов).
    """
    if used_bg is None:
        used_bg = set()
    from downloader import download_audio_from_youtube, write_log, CookiesExpiredError
    from video_processor import process_single_video

    _reload_env()
    topic = os.getenv("CONTENT_TOPIC", "Интересные факты")

    # 1. Скачиваем аудио
    _log_step(f"[Видео {video_index+1}] ⬇ Скачиваю аудио: {video['url']}")
    try:
        audio_path = download_audio_from_youtube(video["url"], index=video_index)
    except CookiesExpiredError:
        log.error(f"[Видео {video_index+1}] Куки протухли — YouTube требует авторизацию!")
        _notify_cookies_expired()
        return []

    if not audio_path:
        log.error(f"[Видео {video_index+1}] Не удалось скачать аудио!")
        return [], used_bg

    # 2. Получаем доступные фоны
    bg_by_category = _get_bg_by_category()
    if not bg_by_category:
        log.error("Нет фоновых видео! Добавьте через бота: /bg → Добавить видео")
        return [], used_bg

    categories = sorted(bg_by_category.keys())

    # Выбираем категорию: стараемся не повторять
    category = categories[video_index % len(categories)]

    bg_videos = bg_by_category[category]

    # 4. Обрабатываем
    _reload_env()
    clips_per = int(os.getenv("CLIPS_PER_VIDEO", "3"))
    clip_dur = int(os.getenv("CLIP_DURATION", "120"))
    _log_step(f"[Видео {video_index+1}] ⚙ Обрабатываю ({clips_per} клипа × {clip_dur} сек)...")
    clips = process_single_video(
        audio_path=audio_path,
        bg_videos=bg_videos,
        video_index=video_index,
        category=category,
        add_subtitles=True,
        bg_by_category=bg_by_category,
        used_bg=used_bg,
    )

    # Удаляем скачанное аудио после обработки
    try:
        os.remove(audio_path)
    except OSError:
        pass

    write_log(f"DONE video={video['title']} clips={len(clips)}")
    return clips, used_bg


def _send_clips(clips: list[str], video: dict):
    """Отправляет клипы в Telegram."""
    from telegram_bot import send_clips_to_telegram

    topic = os.getenv("CONTENT_TOPIC", "")
    video_title = video.get("title", "Reddit история")
    _log_step(f"📤 Отправляю {len(clips)} клипов: «{video_title}»")

    sent = send_clips_to_telegram(
        clips=clips,
        video_title=video_title,
        topic=topic,
        delay_between=DELAY_BETWEEN_SENDS,
    )
    _log_step(f"Отправлено {sent}/{len(clips)} клипов.")
    return sent


def _cleanup_clips(clips: list[str]):
    """Удаляет готовые клипы после отправки."""
    for clip in clips:
        try:
            os.remove(clip)
        except OSError:
            pass


# ─── основной цикл ───────────────────────────────────────────────────────────

def run_cycle():
    """
    Один цикл:
    1. Выбираем VIDEOS_PER_CYCLE видео сразу
    2. Обрабатываем и отправляем по одному
    3. После последней отправки — ждём 24ч
    """
    _log_step("=" * 60)
    _log_step(f"НОВЫЙ ЦИКЛ | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _log_step("=" * 60)

    _reload_env()

    from youtube_search import search_all_channels, get_next_video
    from downloader import write_log

    topic = os.getenv("CONTENT_TOPIC", "Интересные факты")
    _log_step(f"Тема: «{topic}»")

    # ── Шаг 1: выбираем все видео сразу ──────────────────────────
    _log_step(f"Ищу {VIDEOS_PER_CYCLE} видео для цикла...")
    all_videos = search_all_channels(topic)
    if not all_videos:
        log.warning("Видео не найдены. Цикл пропущен.")
        return

    selected = []
    for i in range(VIDEOS_PER_CYCLE):
        video = get_next_video(all_videos)
        if not video:
            log.warning(f"Нашёл только {len(selected)} видео из {VIDEOS_PER_CYCLE}. "
                        f"Смените тему через /settopic.")
            break
        selected.append(video)
        _log_step(f"  [{i+1}/{VIDEOS_PER_CYCLE}] «{video['title']}» "
                  f"({video.get('view_count', 0):,} views)")

    if not selected:
        log.warning("Нет доступных видео. Цикл пропущен.")
        return

    _log_step(f"Выбрано {len(selected)} видео. Начинаю обработку...")

    # ── Шаг 2: обрабатываем и отправляем по одному ───────────────
    total_sent = 0
    used_bg = set()  # трекаем использованные фоновые видео в цикле

    for vi, video in enumerate(selected):
        write_log(f"START video={video['title']}")
        try:
            clips, used_bg = _process_one_video(video, vi, used_bg=used_bg)
            if clips:
                sent = _send_clips(clips, video)
                total_sent += sent
                _cleanup_clips(clips)
        except Exception as e:
            log.error(f"Ошибка при обработке «{video['title']}»: {e}", exc_info=True)

    _log_step(f"Цикл завершён. Отправлено клипов: {total_sent}")


def main():
    log.info("=" * 60)
    log.info("  TikTok Scheduler запущен")
    log.info(f"  Цикл каждые 24 часа")
    log.info(f"  Видео в цикле: {VIDEOS_PER_CYCLE} (по 3 клипа = {VIDEOS_PER_CYCLE * 3} клипов/день)")
    log.info(f"  Тема: {os.getenv('CONTENT_TOPIC', '—')}")
    log.info("  Управление темой: /settopic в Telegram-боте")
    log.info("=" * 60)

    # Запускаем Telegram-бот в фоновом потоке
    try:
        from bot_listener import start_bot_thread
        start_bot_thread()
        log.info("Telegram-бот запущен в фоне.")
    except Exception as e:
        log.warning(f"Telegram-бот не запустился: {e}")

    while True:
        try:
            cycle_start = time.time()
            run_cycle()

            elapsed = time.time() - cycle_start
            wait_sec = max(0, CYCLE_INTERVAL_SEC - elapsed)

            next_run = datetime.now() + timedelta(seconds=wait_sec)
            log.info(f"Следующий цикл в {next_run.strftime('%Y-%m-%d %H:%M:%S')} (через {wait_sec/3600:.1f}ч)")

            # Ждём с логированием каждый час
            waited = 0
            while waited < wait_sec:
                sleep_chunk = min(3600, wait_sec - waited)
                time.sleep(sleep_chunk)
                waited += sleep_chunk
                if waited < wait_sec:
                    remaining = (wait_sec - waited) / 3600
                    log.info(f"  До следующего цикла: {remaining:.1f}ч")

        except KeyboardInterrupt:
            log.info("Остановка по Ctrl+C.")
            sys.exit(0)
        except Exception as e:
            log.error(f"Критическая ошибка в цикле: {e}", exc_info=True)
            log.info("Повтор через 5 минут...")
            time.sleep(300)


if __name__ == "__main__":
    main()
