"""
TikTok Content Generator — главный скрипт.

Пайплайн:
1. Поиск "[тема] реддит" на двух каналах, сортировка по viewCount
2. Берём видео по убыванию популярности (без повторов)
3. yt-dlp скачивает аудио с YouTube
4. Whisper генерирует субтитры (3 клипа по 60 сек на видео)
5. ffmpeg склеивает всё, нарезает на клипы
6. Отправка в Telegram

Тема меняется только вручную через Telegram-бота (/settopic).

Использование:
    python main.py                    — полный пайплайн (1 видео)
    python main.py --count 2          — обработать 2 видео подряд
    python main.py --search-only      — только поиск и показать список
    python main.py --process-only     — только обработка уже скачанного аудио
    python main.py --no-subtitles     — без субтитров
"""

import os
import sys
import argparse
from dotenv import load_dotenv

load_dotenv()


def get_video_count_for_topic() -> int:
    """Считает сколько видео уже обработано (из лога)."""
    log_file = os.path.join(os.getenv("DOWNLOAD_DIR", "logs"), "topic_counter.txt")
    if not os.path.exists(log_file):
        return 0
    with open(log_file, "r", encoding="utf-8") as f:
        try:
            return int(f.read().strip())
        except ValueError:
            return 0


def set_video_count_for_topic(count: int):
    """Записывает счётчик обработанных видео."""
    logs_dir = os.getenv("DOWNLOAD_DIR", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, "topic_counter.txt")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(str(count))


def step_search() -> list[dict]:
    """Шаг 1: Поиск видео на двух каналах по "[тема] реддит"."""
    from youtube_search import search_all_channels

    topic = os.getenv("CONTENT_TOPIC", "Интересные факты")

    print("\n" + "=" * 60)
    print("ЭТАП 1: ПОИСК ВИДЕО")
    print("=" * 60)

    print(f"\n📝 Тема: {topic}")
    all_videos = search_all_channels(topic)

    if not all_videos:
        print("⚠ Видео не найдены по данной теме.")
        return []

    print(f"\n📊 Всего найдено: {len(all_videos)} видео (отсортированы по просмотрам)")

    # Показываем топ
    from youtube_search import load_used_videos
    used = load_used_videos()
    for i, v in enumerate(all_videos[:15]):
        views = v.get("view_count", 0)
        mark = "✓" if v["video_id"] in used else " "
        print(f"  {i+1:>2}. [{mark}] {views:>10,} views │ {v['title'][:60]}")

    return all_videos


def step_get_next_video(all_videos: list[dict]) -> dict | None:
    """Берёт следующее неиспользованное видео."""
    from youtube_search import get_next_video

    video = get_next_video(all_videos)
    if not video:
        print("  ⚠ Все видео по теме использованы!")
        return None

    views = video.get("view_count", 0)
    print(f"  ▶ Следующее видео: \"{video['title']}\" ({views:,} views)")
    print(f"    {video['url']}")

    return video


def step_download_single(video: dict):
    """Скачивает аудио одного видео + фоны по категориям."""
    from downloader import download_audio_from_youtube, download_all_backgrounds

    print("\n" + "=" * 60)
    print("ЭТАП 2: СКАЧИВАНИЕ")
    print("=" * 60)

    # Аудио
    print(f"\n🎵 Скачиваю аудио: {video['url']}")
    audio_path = download_audio_from_youtube(video["url"], index=0)

    if not audio_path:
        print("❌ Не удалось скачать аудио!")
        return None

    return audio_path


def step_download_backgrounds():
    """Скачивает фоны по категориям."""
    from downloader import download_all_backgrounds, get_categories

    categories = get_categories()
    if not categories:
        print("⚠ Нет категорий!")
        return {}

    print(f"\n🎬 Скачиваю фоны: {', '.join(categories)}")
    return download_all_backgrounds()


def step_process(audio_path: str, add_subtitles: bool = True, video_title: str = ""):
    """Шаг 3: Обработка одного видео."""
    from downloader import get_existing_backgrounds_by_category
    from video_processor import process_single_video

    print("\n" + "=" * 60)
    print("ЭТАП 3: ОБРАБОТКА ВИДЕО")
    print("=" * 60)

    bg_by_category = get_existing_backgrounds_by_category()

    if not bg_by_category:
        print("❌ Нет фоновых видео! Добавьте ссылки в backgrounds/*.txt и скачайте.")
        return []

    categories = sorted(bg_by_category.keys())
    video_count = get_video_count_for_topic()

    # AI выбирает лучшую категорию фона для темы
    topic = os.getenv("CONTENT_TOPIC", "")
    try:
        from ai_module import choose_best_bg_category
        category = choose_best_bg_category(topic, categories)
        print(f"\n🤖 AI выбрал категорию фона: {category}")
    except Exception as e:
        print(f"  ⚠ AI не смог выбрать категорию ({e}), ротация...")
        from downloader import get_category_rotation
        category = get_category_rotation(categories, video_count)

    print(f"📁 Категория фона: {category}")
    print(f"📏 Длительность клипа: {os.getenv('CLIP_DURATION', '30')} сек")
    print(f"📝 Субтитры: {'Да' if add_subtitles else 'Нет'}")

    bg_videos = bg_by_category[category]

    clips = process_single_video(
        audio_path=audio_path,
        bg_videos=bg_videos,
        video_index=video_count,
        category=category,
        add_subtitles=add_subtitles,
    )

    print(f"\n{'='*60}")
    print(f"✅ ГОТОВО! Создано {len(clips)} клипов.")
    print(f"📂 Папка: {os.path.abspath(os.getenv('OUTPUT_DIR', 'output'))}")

    for clip in clips:
        print(f"  📹 {clip}")

    return clips


def step_send_telegram(clips: list[str], video: dict):
    """Шаг 4: Отправка клипов в Telegram с AI-метаданными."""
    from telegram_bot import send_clips_to_telegram

    print("\n" + "=" * 60)
    print("ЭТАП 4: ОТПРАВКА В TELEGRAM")
    print("=" * 60)

    topic = os.getenv("CONTENT_TOPIC", "")
    video_title = video.get("title", "Reddit видео")

    sent = send_clips_to_telegram(
        clips=clips,
        video_title=video_title,
        topic=topic,
        delay_between=2.0,
    )

    if sent > 0:
        print(f"✅ Отправлено {sent}/{len(clips)} клипов в Telegram!")
    else:
        print("⚠ Не удалось отправить клипы в Telegram")


def main():
    parser = argparse.ArgumentParser(description="TikTok Content Generator")
    parser.add_argument("--count", type=int, default=1,
                        help="Сколько видео обработать за раз (по умолчанию 1)")
    parser.add_argument("--search-only", action="store_true",
                        help="Только поиск видео")
    parser.add_argument("--process-only", action="store_true",
                        help="Только обработка существующих файлов")
    parser.add_argument("--no-subtitles", action="store_true",
                        help="Не добавлять субтитры")
    args = parser.parse_args()

    print("""
╔══════════════════════════════════════════════════════════╗
║           🎬 TikTok Content Generator 🎬               ║
║                                                          ║
║  Groq AI + YouTube API + yt-dlp + Whisper + ffmpeg       ║
║  Два канала · Без повторов · Авто-темы                  ║
╚══════════════════════════════════════════════════════════╝
    """)

    # Проверяем ключи
    if not os.getenv("GROQ_API_KEY"):
        print("❌ GROQ_API_KEY не найден в .env!")
        sys.exit(1)
    if not os.getenv("YOUTUBE_API_KEY"):
        print("❌ YOUTUBE_API_KEY не найден в .env!")
        sys.exit(1)

    from downloader import write_log, get_categories
    logs_dir = os.getenv("DOWNLOAD_DIR", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    write_log(f"=== СТАРТ === args={sys.argv[1:]} topic={os.getenv('CONTENT_TOPIC','')}")
    print(f"📋 Лог: {os.path.abspath(os.path.join(logs_dir, 'run_log.txt'))}")

    categories = get_categories()
    if categories:
        print(f"📂 Категории фона: {', '.join(categories)}")
    else:
        print("⚠ Нет категорий! Создайте .txt файлы в папке backgrounds/")

    channels = os.getenv("YOUTUBE_CHANNELS", "")
    print(f"📺 Каналы: {channels}")

    # --- Только поиск ---
    if args.search_only:
        topic = os.getenv("CONTENT_TOPIC", "Интересные факты")
        print(f"\n📝 Тема: {topic}")
        step_search()
        write_log("=== ЗАВЕРШЕНО (search-only) ===")
        return

    # --- Только обработка ---
    if args.process_only:
        from downloader import get_existing_audio
        audio_files = get_existing_audio()
        if audio_files:
            clips = step_process(audio_files[0], add_subtitles=not args.no_subtitles,
                                 video_title="Reddit история")
            if clips:
                step_send_telegram(clips, {"title": "Reddit история"})
        else:
            print("❌ Нет аудио файлов!")
        write_log("=== ЗАВЕРШЕНО (process-only) ===")
        return

    # --- Полный пайплайн ---
    for i in range(args.count):
        if args.count > 1:
            print(f"\n{'#'*60}")
            print(f"# ВИДЕО {i+1} / {args.count}")
            print(f"{'#'*60}")

        topic = os.getenv("CONTENT_TOPIC", "Интересные факты")
        print(f"\n📝 Тема: \"{topic}\"")

        # 1. Поиск
        all_videos = step_search()
        if not all_videos:
            print("❌ Нет видео, останавливаюсь.")
            break

        # 2. Берём следующее неиспользованное
        video = step_get_next_video(all_videos)
        if not video:
            print("❌ Все видео по теме использованы. Смените тему через Telegram-бота (/settopic).")
            break

        # 3. Скачиваем аудио
        audio_path = step_download_single(video)
        if not audio_path:
            continue

        # 4. Обрабатываем
        clips = step_process(audio_path, add_subtitles=not args.no_subtitles,
                             video_title=video.get("title", ""))

        # 5. Отправляем в Telegram
        if clips:
            step_send_telegram(clips, video)

        # 6. Увеличиваем счётчик
        current = get_video_count_for_topic()
        set_video_count_for_topic(current + 1)

        write_log(f"DONE video={video['title']} clips={len(clips)}")
        write_log(f"DONE video={video['title']} clips={len(clips)}")

    write_log("=== ЗАВЕРШЕНО ===")


if __name__ == "__main__":
    main()
