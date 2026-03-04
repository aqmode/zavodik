"""
Модуль для скачивания контента.
- Аудио с YouTube (yt-dlp) → logs/audio/
- Фоновые видео из YouTube Shorts по категориям (yt-dlp) → logs/background/<категория>/
  Поиск: "[категория] satisfying video", скачивается со звуком, потом звук убирается ffmpeg
- Лог-файл каждого запуска → logs/run_log.txt
"""

import subprocess
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "logs")
BACKGROUNDS_DIR = os.getenv("BACKGROUNDS_DIR", "backgrounds")
AUDIO_DIR = os.path.join(DOWNLOAD_DIR, "audio")
BG_BASE_DIR = os.path.join(DOWNLOAD_DIR, "background")
LOG_FILE = os.path.join(DOWNLOAD_DIR, "run_log.txt")
# Сколько Shorts скачивать на категорию
BG_SHORTS_COUNT = int(os.getenv("BG_SHORTS_COUNT", "3"))


def ensure_dirs():
    """Создаёт папки для загрузок."""
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(BG_BASE_DIR, exist_ok=True)


def write_log(message: str):
    """Дописывает строку в лог-файл с временной меткой."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {message}\n")


def _read_links(filepath: str) -> list[str]:
    """Читает ссылки из текстового файла."""
    links = []
    if not os.path.exists(filepath):
        return links
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                links.append(line)
    return links


# ==================== КАТЕГОРИИ ====================

def get_categories() -> list[str]:
    """
    Сканирует папку backgrounds/ и возвращает список категорий.
    Каждый .txt файл = категория (имя файла = тема поиска фона).
    """
    categories = []
    if not os.path.exists(BACKGROUNDS_DIR):
        os.makedirs(BACKGROUNDS_DIR, exist_ok=True)
        return categories

    for f in sorted(os.listdir(BACKGROUNDS_DIR)):
        if f.endswith(".txt") and f != "README.txt":
            name = os.path.splitext(f)[0]
            categories.append(name)

    return categories


# ==================== СКАЧИВАНИЕ АУДИО ====================

def download_audio_from_youtube(url: str, index: int = 0) -> str | None:
    """
    Скачивает аудио с YouTube видео через прокси.
    Прогресс yt-dlp выводится напрямую в stdout.
    """
    ensure_dirs()
    output_path = os.path.join(AUDIO_DIR, f"audio_{index:03d}.%(ext)s")

    # Прокси: берём из .env PROXY или YTDLP_PROXY
    # Формат .env: http://user:pass@host:port
    proxy = os.getenv("YTDLP_PROXY") or os.getenv("PROXY") or None

    # Путь к deno для решения YouTube n-challenge
    deno_path = os.path.join(os.path.expanduser("~"), ".deno", "bin", "deno.exe")

    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--output", output_path,
        "--no-playlist",
        "--newline",
    ]

    # Добавляем deno как JS runtime если найден
    if os.path.exists(deno_path):
        cmd += ["--js-runtimes", f"deno:{deno_path}"]

    if proxy:
        cmd += ["--proxy", proxy]

    # Cookies для обхода bot-detection YouTube
    cookies_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    if os.path.exists(cookies_file):
        cmd += ["--cookies", cookies_file]

    cmd.append(url)

    # Передаём deno в PATH процесса
    env = os.environ.copy()
    deno_bin = os.path.join(os.path.expanduser("~"), ".deno", "bin")
    env["PATH"] = env.get("PATH", "") + os.pathsep + deno_bin

    try:
        print(f"  Скачиваю аудио: {url}")
        if proxy:
            print(f"  Прокси: {proxy.split('@')[-1]}")  # показываем только host:port
        result = subprocess.run(cmd, text=True, env=env)

        # Ищем скачанный файл
        expected = os.path.join(AUDIO_DIR, f"audio_{index:03d}.mp3")
        if os.path.exists(expected):
            print(f"  ✓ Сохранено: {expected}")
            write_log(f"AUDIO OK  {expected}  <-  {url}")
            return expected

        for f in os.listdir(AUDIO_DIR):
            if f.startswith(f"audio_{index:03d}"):
                full_path = os.path.join(AUDIO_DIR, f)
                print(f"  ✓ Сохранено: {full_path}")
                write_log(f"AUDIO OK  {full_path}  <-  {url}")
                return full_path

        # Файл не найден — выводим ошибку
        print(f"  ✗ Аудио не скачано (код {result.returncode})")
        write_log(f"AUDIO ERR {url}  //  exit code {result.returncode}")

    except Exception as e:
        print(f"  ✗ Ошибка: {e}")
        write_log(f"AUDIO ERR {url}  //  {e}")

    return None


def download_all_audio(links_file: str = "youtube_links.txt") -> list[str]:
    """Скачивает аудио со всех ссылок из файла."""
    links = _read_links(links_file)
    if not links:
        print("Нет ссылок для скачивания аудио.")
        return []

    audio_files = []
    for i, url in enumerate(links):
        result = download_audio_from_youtube(url, i)
        if result:
            audio_files.append(result)

    print(f"\nСкачано {len(audio_files)}/{len(links)} аудио файлов.")
    return audio_files


# ==================== СКАЧИВАНИЕ ФОНА: YouTube Shorts ====================

def _remove_audio_from_video(input_path: str) -> str:
    """Убирает аудио из видео через ffmpeg. Возвращает путь к файлу без звука."""
    base, ext = os.path.splitext(input_path)
    silent_path = f"{base}_silent{ext}"

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-an",  # убираем аудио
        "-c:v", "copy",
        silent_path,
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        # Заменяем оригинал тихим
        os.remove(input_path)
        os.rename(silent_path, input_path)
        return input_path
    except Exception as e:
        print(f"    ⚠ Не удалось убрать звук: {e}")
        # Если не получилось — оставляем как есть
        if os.path.exists(silent_path):
            try:
                os.remove(silent_path)
            except OSError:
                pass
        return input_path


def search_and_download_shorts(category: str, count: int = 3) -> list[str]:
    """
    Ищет YouTube Shorts по запросу "[категория] satisfying video shorts"
    и скачивает первые count видео. Убирает звук.
    Возвращает список путей к скачанным файлам.
    """
    cat_dir = os.path.join(BG_BASE_DIR, category)
    os.makedirs(cat_dir, exist_ok=True)

    # Пропускаем если уже есть достаточно видео
    existing = [f for f in os.listdir(cat_dir) if f.endswith(('.mp4', '.webm', '.mkv'))]
    if len(existing) >= count:
        print(f"  ✓ Уже есть {len(existing)} видео, пропускаю")
        return [os.path.join(cat_dir, f) for f in sorted(existing)]

    need = count - len(existing)
    search_term = f"{category.replace('_', ' ')} satisfying video shorts"
    print(f"  🔍 Ищу: \"{search_term}\" (нужно ещё {need})")

    # ШАГ 1: Получаем список ID видео через поиск
    search_cmd = [
        "yt-dlp",
        "--print", "id",
        "--no-download",
        "--flat-playlist",
        f"ytsearch{need + 2}:{search_term}",  # +2 запас
    ]

    try:
        search_result = subprocess.run(
            search_cmd,
            capture_output=True, text=True,
            timeout=60,
        )
        video_ids = [line.strip() for line in search_result.stdout.strip().splitlines() if line.strip()]
    except subprocess.TimeoutExpired:
        print(f"  ✗ Таймаут поиска")
        write_log(f"BG ERR    [{category}]  таймаут поиска")
        return [os.path.join(cat_dir, f) for f in existing]
    except Exception as e:
        print(f"  ✗ Ошибка поиска: {e}")
        write_log(f"BG ERR    [{category}]  //  {e}")
        return [os.path.join(cat_dir, f) for f in existing]

    if not video_ids:
        print(f"  ✗ Не найдено видео")
        write_log(f"BG ERR    [{category}]  нет результатов поиска")
        return [os.path.join(cat_dir, f) for f in existing]

    print(f"  📋 Найдено {len(video_ids)} видео, скачиваю {need}...")

    # ШАГ 2: Скачиваем каждое видео по отдельности
    downloaded = [os.path.join(cat_dir, f) for f in existing]
    idx = len(existing)

    for vid_id in video_ids[:need]:
        idx += 1
        out_path = os.path.join(cat_dir, f"bg_{idx:03d}.mp4")
        url = f"https://www.youtube.com/watch?v={vid_id}"

        dl_cmd = [
            "yt-dlp",
            "--format", "best[ext=mp4][height<=1920]/best[ext=mp4]/best",
            "--output", out_path,
            "--socket-timeout", "30",
            "--no-warnings",
            url,
        ]

        try:
            print(f"  📥 [{idx}/{count}] {vid_id}...", end=" ", flush=True)
            proc = subprocess.run(
                dl_cmd,
                capture_output=True, text=True,
                timeout=120,  # 2 мин на одно видео
            )

            if os.path.isfile(out_path):
                _remove_audio_from_video(out_path)
                print(f"✓ (без звука)")
                write_log(f"BG OK     [{category}]  {out_path}  vid={vid_id}")
                downloaded.append(out_path)
            else:
                # Проверяем не скачалось ли с другим расширением
                base = os.path.splitext(out_path)[0]
                found = False
                for ext in ['.mp4', '.webm', '.mkv']:
                    alt = base + ext
                    if os.path.isfile(alt):
                        _remove_audio_from_video(alt)
                        print(f"✓ {ext} (без звука)")
                        write_log(f"BG OK     [{category}]  {alt}  vid={vid_id}")
                        downloaded.append(alt)
                        found = True
                        break
                if not found:
                    err = proc.stderr[-200:] if proc.stderr else "?"
                    print(f"✗ ({err.strip()[:80]})")
                    write_log(f"BG ERR    [{category}]  vid={vid_id}  //  {err[:200]}")

        except subprocess.TimeoutExpired:
            print(f"✗ таймаут")
            write_log(f"BG ERR    [{category}]  vid={vid_id}  таймаут")
        except Exception as e:
            print(f"✗ {e}")
            write_log(f"BG ERR    [{category}]  vid={vid_id}  //  {e}")

    return downloaded


def download_all_backgrounds() -> dict[str, list[str]]:
    """
    Скачивает фоновые видео из YouTube Shorts для ВСЕХ категорий.
    Поиск: "[категория] satisfying video".
    Возвращает {категория: [пути к файлам]}.
    """
    categories = get_categories()

    if not categories:
        print("Нет категорий! Создайте .txt файлы в backgrounds/")
        return {}

    result = {}
    for category in categories:
        print(f"\n📁 Категория: {category}")
        files = search_and_download_shorts(category, count=BG_SHORTS_COUNT)
        if files:
            result[category] = files

    return result


# ==================== ПОЛУЧЕНИЕ СУЩЕСТВУЮЩИХ ФАЙЛОВ ====================

def get_existing_audio() -> list[str]:
    """Возвращает список уже скачанных аудио файлов."""
    ensure_dirs()
    files = []
    for f in sorted(os.listdir(AUDIO_DIR)):
        if f.endswith(('.mp3', '.m4a', '.wav', '.opus')):
            files.append(os.path.join(AUDIO_DIR, f))
    return files


def get_existing_backgrounds_by_category() -> dict[str, list[str]]:
    """
    Возвращает словарь {категория: [пути к видео]} для уже скачанных фонов.
    Автоматически подхватывает все подпапки в downloads/background/.
    """
    ensure_dirs()
    result = {}

    if not os.path.exists(BG_BASE_DIR):
        return result

    for cat_name in sorted(os.listdir(BG_BASE_DIR)):
        cat_dir = os.path.join(BG_BASE_DIR, cat_name)
        if not os.path.isdir(cat_dir):
            continue

        files = []
        for f in sorted(os.listdir(cat_dir)):
            if f.endswith(('.mp4', '.webm', '.mkv')):
                files.append(os.path.join(cat_dir, f))

        if files:
            result[cat_name] = files

    return result


def get_category_rotation(categories: list[str], video_index: int) -> str:
    """
    Возвращает категорию для данного видео по принципу ротации.
    Видео 0 → категория 0, Видео 1 → категория 1, ...
    Когда категории заканчиваются — начинаем с начала.
    """
    if not categories:
        raise ValueError("Нет доступных категорий!")
    return categories[video_index % len(categories)]


if __name__ == "__main__":
    print("=== Тест скачивания ===")
    print(f"Аудио папка: {AUDIO_DIR}")
    print(f"Фон папка: {BG_BASE_DIR}")
    print(f"Папка категорий: {BACKGROUNDS_DIR}")
    print(f"Shorts на категорию: {BG_SHORTS_COUNT}")

    cats = get_categories()
    print(f"\nКатегории: {cats}")

    print(f"\nСуществующие аудио: {len(get_existing_audio())}")
    bg = get_existing_backgrounds_by_category()
    print(f"Существующие фоны:")
    for cat, files in bg.items():
        print(f"  {cat}: {len(files)} видео")
