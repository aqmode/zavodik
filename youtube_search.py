"""
Модуль для поиска видео на YouTube через YouTube Data API v3.

Логика:
- Два канала (из YOUTUBE_CHANNELS в .env)
- Поиск: "[тема] реддит" на каждом канале
- Сортировка по viewCount (убывание)
- Без повторов: used_videos.txt хранит уже использованные video_id
- get_most_popular_unseen() — берёт самое популярное неиспользованное видео
- get_next_video_for_topic() — следующее видео по убыванию популярности
"""

from googleapiclient.discovery import build
from dotenv import load_dotenv
import os
import re

load_dotenv()

USED_VIDEOS_FILE = "used_videos.txt"


def get_youtube_service():
    """Создаёт клиент YouTube Data API."""
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise ValueError("YOUTUBE_API_KEY не найден в .env")
    return build("youtube", "v3", developerKey=api_key)


def get_channel_ids() -> list[dict]:
    """
    Получает ID всех каналов из YOUTUBE_CHANNELS (через запятую).
    Возвращает [{url, channel_id, name}, ...]
    """
    channels_str = os.getenv("YOUTUBE_CHANNELS", "")
    if not channels_str:
        return []

    channels = []
    for url in channels_str.split(","):
        url = url.strip()
        if not url:
            continue
        channel_id = _resolve_channel_id(url)
        if channel_id:
            channels.append({"url": url, "channel_id": channel_id})
            print(f"  ✓ Канал: {url} → {channel_id}")
        else:
            print(f"  ✗ Не найден: {url}")

    return channels


def _resolve_channel_id(channel_url: str) -> str | None:
    """Получает ID канала по URL."""
    youtube = get_youtube_service()

    # Прямой ID
    match = re.search(r'/channel/(UC[\w-]+)', channel_url)
    if match:
        return match.group(1)

    # Handle или custom URL
    handle_match = re.search(r'/@([\w.-]+)', channel_url)
    custom_match = re.search(r'/c/([\w.-]+)', channel_url)
    search_term = None
    if handle_match:
        search_term = handle_match.group(1)
    elif custom_match:
        search_term = custom_match.group(1)

    if not search_term:
        return None

    # Через forHandle
    try:
        request = youtube.channels().list(part="id", forHandle=search_term)
        response = request.execute()
        if response.get("items"):
            return response["items"][0]["id"]
    except Exception:
        pass

    # Фоллбэк: поиск
    try:
        request = youtube.search().list(
            part="snippet", q=search_term, type="channel", maxResults=1
        )
        response = request.execute()
        if response.get("items"):
            return response["items"][0]["snippet"]["channelId"]
    except Exception:
        pass

    return None


def search_videos_on_channel(
    channel_id: str,
    query: str,
    max_results: int = 25
) -> list[dict]:
    """
    Ищет видео на канале по запросу.
    Возвращает список с title, video_id, url.
    """
    youtube = get_youtube_service()

    request = youtube.search().list(
        part="snippet",
        channelId=channel_id,
        q=query,
        type="video",
        order="viewCount",
        maxResults=max_results,
    )
    response = request.execute()

    video_ids = []
    videos_basic = []
    for item in response.get("items", []):
        vid = item["id"]["videoId"]
        video_ids.append(vid)
        videos_basic.append({
            "title": item["snippet"]["title"],
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": item["snippet"].get("channelTitle", ""),
        })

    # Получаем реальное количество просмотров
    if video_ids:
        stats = _get_video_stats(video_ids)
        for v in videos_basic:
            v["view_count"] = stats.get(v["video_id"], 0)

    # Сортируем по просмотрам (убывание)
    videos_basic.sort(key=lambda x: x.get("view_count", 0), reverse=True)

    return videos_basic


def _get_video_stats(video_ids: list[str]) -> dict[str, int]:
    """Получает viewCount для списка видео."""
    youtube = get_youtube_service()
    stats = {}

    # API принимает до 50 id за раз
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        request = youtube.videos().list(
            part="statistics",
            id=",".join(batch)
        )
        response = request.execute()
        for item in response.get("items", []):
            vid = item["id"]
            views = int(item["statistics"].get("viewCount", 0))
            stats[vid] = views

    return stats


def search_all_channels(topic: str, max_per_channel: int = 25) -> list[dict]:
    """
    Ищет "[тема] реддит" на ВСЕХ каналах.
    Возвращает объединённый список, отсортированный по viewCount.
    """
    channels = get_channel_ids()
    if not channels:
        print("❌ Нет каналов! Задайте YOUTUBE_CHANNELS в .env")
        return []

    query = f"{topic} реддит"
    print(f"  🔍 Поисковый запрос: \"{query}\"")

    all_videos = []
    for ch in channels:
        print(f"  📺 Канал: {ch['url']}")
        videos = search_videos_on_channel(ch["channel_id"], query, max_per_channel)
        all_videos.extend(videos)
        print(f"     Найдено: {len(videos)} видео")

    # Убираем дубликаты (по video_id)
    seen = set()
    unique = []
    for v in all_videos:
        if v["video_id"] not in seen:
            seen.add(v["video_id"])
            unique.append(v)

    # Сортируем по просмотрам
    unique.sort(key=lambda x: x.get("view_count", 0), reverse=True)

    return unique


# ==================== ИСПОЛЬЗУЕМЫЕ ВИДЕО (без повторов) ====================

def load_used_videos() -> set[str]:
    """Загружает множество video_id которые уже были использованы."""
    used = set()
    if os.path.exists(USED_VIDEOS_FILE):
        with open(USED_VIDEOS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    used.add(line)
    return used


def mark_video_used(video_id: str):
    """Добавляет video_id в список использованных."""
    with open(USED_VIDEOS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{video_id}\n")


def get_next_video(all_videos: list[dict]) -> dict | None:
    """
    Возвращает следующее неиспользованное видео (по убыванию viewCount).
    Помечает его как использованное.
    """
    used = load_used_videos()

    for video in all_videos:
        if video["video_id"] not in used:
            mark_video_used(video["video_id"])
            return video

    return None  # все видео использованы


def get_most_popular_unseen(all_videos: list[dict]) -> dict | None:
    """
    Возвращает самое популярное неиспользованное видео БЕЗ пометки.
    Используется для генерации темы.
    """
    used = load_used_videos()
    for video in all_videos:
        if video["video_id"] not in used:
            return video
    return None


def save_links_to_file(videos: list[dict], filepath: str = "youtube_links.txt"):
    """Сохраняет ссылки в файл (без дубликатов)."""
    existing = set()
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    existing.add(line)

    new_count = 0
    with open(filepath, "a", encoding="utf-8") as f:
        for video in videos:
            url = video["url"]
            if url not in existing:
                f.write(f"{url}\n")
                existing.add(url)
                new_count += 1
                views = video.get("view_count", 0)
                print(f"  + [{views:,} views] {video['title']}")

    print(f"  Добавлено {new_count} ссылок в {filepath}")
    return new_count


def read_links_from_file(filepath: str) -> list[str]:
    """Читает ссылки из файла."""
    links = []
    if not os.path.exists(filepath):
        return links
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                links.append(line)
    return links


if __name__ == "__main__":
    topic = os.getenv("CONTENT_TOPIC", "интересные факты")
    print(f"Тема: {topic}")
    print(f"Каналы: {os.getenv('YOUTUBE_CHANNELS', '')}\n")

    print("=== Поиск на всех каналах ===")
    videos = search_all_channels(topic)

    print(f"\n=== Топ-10 по просмотрам ===")
    for i, v in enumerate(videos[:10]):
        views = v.get("view_count", 0)
        used = "✓" if v["video_id"] in load_used_videos() else " "
        print(f"  {i+1}. [{used}] [{views:>10,} views] {v['title']}")

    next_v = get_next_video(videos)
    if next_v:
        print(f"\n=== Следующее видео ===")
        print(f"  {next_v['title']} ({next_v['url']})")
