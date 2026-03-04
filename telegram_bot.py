"""
Модуль для отправки готовых видео в Telegram через Bot API.

Функции:
- send_video_to_telegram() — отправляет видео файл с caption (название + описание + хештеги)
- send_clips_to_telegram() — отправляет список клипов с метаданными

Использует httpx для HTTP запросов (через прокси если указан).
"""

import httpx
import os
import time
from dotenv import load_dotenv

load_dotenv()


def _get_bot_config() -> dict:
    """Получает конфигурацию бота из .env."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не найден в .env!")
    if not chat_id:
        raise ValueError("TELEGRAM_CHAT_ID не найден в .env!")

    proxy = os.getenv("PROXY") or None

    return {
        "token": token,
        "chat_id": chat_id,
        "proxy": proxy,
    }


def send_message_to_telegram(text: str) -> bool:
    """
    Отправляет текстовое сообщение в Telegram.
    Возвращает True при успехе.
    """
    try:
        config = _get_bot_config()
    except ValueError as e:
        print(f"  ⚠ Telegram: {e}")
        return False

    url = f"https://api.telegram.org/bot{config['token']}/sendMessage"
    payload = {
        "chat_id": config["chat_id"],
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        client_kwargs = {"timeout": 30.0}
        if config["proxy"]:
            client_kwargs["proxy"] = config["proxy"]

        with httpx.Client(**client_kwargs) as client:
            response = client.post(url, json=payload)

        if response.status_code == 200:
            result = response.json()
            if result.get("ok"):
                return True
            else:
                print(f"  ⚠ Telegram API error: {result.get('description', '?')}")
                return False
        else:
            print(f"  ⚠ Telegram HTTP {response.status_code}: {response.text[:200]}")
            return False

    except Exception as e:
        print(f"  ✗ Ошибка отправки в Telegram: {e}")
        return False


def send_video_to_telegram(video_path: str, caption: str = "") -> bool:
    """
    Отправляет видео файл в Telegram с caption.
    
    video_path: путь к .mp4 файлу
    caption: текст под видео (название + описание + хештеги)
    
    Telegram ограничение: видео до 50 МБ через Bot API.
    Caption: до 1024 символов.
    
    Возвращает True при успехе.
    """
    if not os.path.exists(video_path):
        print(f"  ✗ Файл не найден: {video_path}")
        return False

    file_size = os.path.getsize(video_path)
    if file_size > 50 * 1024 * 1024:  # 50 МБ
        print(f"  ⚠ Файл слишком большой ({file_size // 1024 // 1024} МБ > 50 МБ), пропускаю")
        return False

    try:
        config = _get_bot_config()
    except ValueError as e:
        print(f"  ⚠ Telegram: {e}")
        return False

    url = f"https://api.telegram.org/bot{config['token']}/sendVideo"

    # Обрезаем caption до 1024 символов (лимит Telegram)
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    try:
        client_kwargs = {"timeout": 120.0}  # 2 мин для загрузки видео
        if config["proxy"]:
            client_kwargs["proxy"] = config["proxy"]

        with httpx.Client(**client_kwargs) as client:
            with open(video_path, "rb") as video_file:
                files = {"video": (os.path.basename(video_path), video_file, "video/mp4")}
                data = {
                    "chat_id": config["chat_id"],
                    "caption": caption,
                    "supports_streaming": "true",
                }
                response = client.post(url, data=data, files=files)

        if response.status_code == 200:
            result = response.json()
            if result.get("ok"):
                print(f"  ✓ Отправлено в Telegram: {os.path.basename(video_path)}")
                return True
            else:
                print(f"  ⚠ Telegram API error: {result.get('description', '?')}")
                return False
        elif response.status_code == 429:
            # Rate limit — ждём
            retry_after = 10
            try:
                retry_after = response.json().get("parameters", {}).get("retry_after", 10)
            except Exception:
                pass
            print(f"  ⏳ Rate limit, жду {retry_after} сек...")
            time.sleep(retry_after)
            return send_video_to_telegram(video_path, caption)  # рекурсивный retry
        else:
            print(f"  ⚠ Telegram HTTP {response.status_code}: {response.text[:200]}")
            return False

    except Exception as e:
        print(f"  ✗ Ошибка отправки видео в Telegram: {e}")
        return False


def send_clips_to_telegram(
    clips: list[str],
    video_title: str,
    topic: str,
    delay_between: float = 2.0,
) -> int:
    """
    Отправляет список клипов в Telegram.
    Для каждого клипа генерирует AI метаданные (название, описание, хештеги).
    
    clips: список путей к .mp4 файлам
    video_title: название исходного видео (для AI)
    topic: тема контента (для AI)
    delay_between: задержка между отправками (секунды)
    
    Возвращает количество успешно отправленных.
    """
    if not clips:
        print("  ⚠ Нет клипов для отправки")
        return 0

    # Генерируем метаданные через AI
    try:
        from ai_module import generate_video_metadata
        print(f"\n📱 Генерирую метаданные для Telegram...")
        metadata = generate_video_metadata(video_title, topic)
        caption = metadata["caption"]
        print(f"  📝 Название: {metadata['title']}")
        print(f"  📝 Описание: {metadata['description']}")
        print(f"  📝 Хештеги: {metadata['hashtags']}")
    except Exception as e:
        print(f"  ⚠ Ошибка генерации метаданных: {e}")
        # Фоллбэк caption
        caption = f"{video_title}\n#reddit #реддит #реддитистории"

    sent = 0
    total = len(clips)

    print(f"\n📤 Отправляю {total} клипов в Telegram...")

    for i, clip_path in enumerate(clips):
        # Для каждого клипа добавляем номер части
        if total > 1:
            clip_caption = f"{caption}\n\n📎 Часть {i+1}/{total}"
        else:
            clip_caption = caption

        print(f"  [{i+1}/{total}] {os.path.basename(clip_path)}...", end=" ", flush=True)

        if send_video_to_telegram(clip_path, clip_caption):
            sent += 1
        else:
            print()  # новая строка после ошибки

        # Задержка между отправками (чтобы не словить rate limit)
        if i < total - 1 and delay_between > 0:
            time.sleep(delay_between)

    print(f"\n✅ Отправлено {sent}/{total} клипов в Telegram")
    return sent


if __name__ == "__main__":
    print("=== Тест Telegram бота ===")

    # Тест текстового сообщения
    print("\nОтправляю тестовое сообщение...")
    ok = send_message_to_telegram("🤖 Тест: TikTok Content Generator работает!")
    print(f"Результат: {'✓ Успех' if ok else '✗ Ошибка'}")
