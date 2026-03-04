"""
Модуль для работы с AI через Groq Cloud API.
Groq — сверхбыстрый inference (LPU). OpenAI-совместимый формат.

Функции:
- generate_topic_from_title() — из названия видео делает тему 2-3 слова
- update_topic_in_env() — записывает новую тему в .env
- generate_subtitles_correction() — коррекция субтитров
"""

import httpx
from openai import OpenAI
from dotenv import load_dotenv
import os
import re

load_dotenv()

# Модели-фоллбэки на Groq
_MODELS_FALLBACK = [
    "llama-3.3-70b-versatile",
    "gemma2-9b-it",
    "mixtral-8x7b-32768",
]


def _get_proxy() -> str | None:
    """Возвращает прокси из .env или None."""
    return os.getenv("PROXY") or None


def _get_client() -> OpenAI:
    """Создаёт OpenAI-совместимый клиент для Groq (через прокси)."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY не найден в .env")

    proxy = _get_proxy()
    http_client = None
    if proxy:
        http_client = httpx.Client(proxy=proxy)

    return OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=api_key,
        http_client=http_client,
    )


def _chat(prompt: str, max_tokens: int = 256) -> str:
    """
    Отправляет запрос через Groq с fallback моделей. Без ожиданий.
    """
    client = _get_client()
    preferred_model = os.getenv("AI_MODEL", _MODELS_FALLBACK[0])
    models_to_try = [preferred_model] + [m for m in _MODELS_FALLBACK if m != preferred_model]

    for model in models_to_try:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=max_tokens,
            )
            text = response.choices[0].message.content
            if text:
                return text.strip()
        except Exception as e:
            print(f"  ⚠ Ошибка ({model}): {str(e)[:150]}")
            continue

    raise RuntimeError("Все модели Groq недоступны. Проверьте ключ и прокси.")


def generate_topic_from_title(video_title: str) -> str:
    """
    Из названия YouTube видео генерирует обобщённую тему (2-3 слова, русский).
    Например: "Самые странные вещи на Reddit #34" → "Странные вещи"
    """
    prompt = f"""Из названия видео сделай короткую тему для поиска (2-3 слова, русский язык).
Убери номера, хештеги, лишние слова. Оставь только суть.

Название: "{video_title}"

Верни ТОЛЬКО тему, ничего больше. Например: "Худшее первое свидание" или "Странные истории"."""

    result = _chat(prompt)
    result = result.strip('"\'').strip()
    result = result.split('\n')[0].strip()
    return result


def update_topic_in_env(new_topic: str, env_path: str = ".env"):
    """
    Записывает новую тему в .env файл (заменяет CONTENT_TOPIC=...).
    """
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_content = re.sub(
        r'^CONTENT_TOPIC=.*$',
        f'CONTENT_TOPIC={new_topic}',
        content,
        flags=re.MULTILINE
    )

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    os.environ["CONTENT_TOPIC"] = new_topic
    print(f"  📝 Новая тема в .env: {new_topic}")


def generate_subtitles_correction(raw_text: str) -> str:
    """Корректирует субтитры — ошибки распознавания, пунктуация."""
    # Ограничиваем текст чтобы не превысить контекст модели
    if len(raw_text) > 3000:
        raw_text = raw_text[:3000]

    prompt = f"""Исправь субтитры (русский). Не меняй смысл, только ошибки и пунктуацию.
Верни ТОЛЬКО исправленный текст.

{raw_text}"""
    return _chat(prompt, max_tokens=2048)


def generate_video_metadata(video_title: str, topic: str) -> dict:
    """
    Генерирует метаданные для готового клипа:
    - title: цепляющее название для TikTok/Shorts (русский)
    - description: обобщение названия видео, максимум 20 слов
    - hashtags: строка из 5 хештегов (2 AI по теме + #reddit #реддит #реддитистории)

    Возвращает dict с ключами title, description, hashtags, caption (всё вместе).
    """
    prompt = f"""Ты — копирайтер для TikTok/YouTube Shorts. На основе информации сгенерируй:

1. НАЗВАНИЕ — цепляющее, короткое, русский язык, вызывает любопытство (макс 4-5 слов)
2. ОПИСАНИЕ — обобщение названия видео, максимум 20 слов, русский язык
3. ДВА ХЕШТЕГА — по теме видео, релевантные, русский или английский, начинаются с #

Название видео: "{video_title}"
Тема: "{topic}"

Ответь СТРОГО в формате (каждое на новой строке):
НАЗВАНИЕ: ...
ОПИСАНИЕ: ...
ХЕШТЕГИ: #тег1 #тег2"""

    result = _chat(prompt)

    # Парсим ответ
    title = ""
    description = ""
    ai_hashtags = ""

    for line in result.strip().split("\n"):
        line = line.strip()
        if line.upper().startswith("НАЗВАНИЕ:"):
            title = line.split(":", 1)[1].strip().strip('"\'')
        elif line.upper().startswith("ОПИСАНИЕ:"):
            description = line.split(":", 1)[1].strip().strip('"\'')
        elif line.upper().startswith("ХЕШТЕГИ:"):
            ai_hashtags = line.split(":", 1)[1].strip()

    # Фоллбэк если AI не выдал нормальный ответ
    if not title:
        title = video_title[:60]
    if not description:
        description = f"История из Reddit: {topic}"

    # Гарантируем макс 20 слов в описании
    desc_words = description.split()
    if len(desc_words) > 20:
        description = " ".join(desc_words[:20])

    # Собираем 5 хештегов: 2 AI + 3 фиксированных
    fixed_tags = "#reddit #реддит #реддитистории"

    # Извлекаем AI хештеги (только первые 2)
    ai_tags_list = [t.strip() for t in ai_hashtags.split() if t.strip().startswith("#")]
    ai_tags_str = " ".join(ai_tags_list[:2])

    if ai_tags_str:
        hashtags = f"{ai_tags_str} {fixed_tags}"
    else:
        hashtags = f"#{topic.replace(' ', '')} {fixed_tags}"

    # Собираем caption: название\nописание\nхештеги — всё в одном сообщении
    caption = f"{title}\n{description}\n{hashtags}"

    return {
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "caption": caption,
    }


def choose_best_bg_category(topic: str, categories: list[str]) -> str:
    """
    AI выбирает лучшую категорию фона для данной темы.
    Например: тема "Страшные истории" → категория "glass_cleaning" (спокойный фон).
    Если AI не может выбрать, возвращает первую категорию.
    """
    if not categories:
        raise ValueError("Нет доступных категорий!")
    if len(categories) == 1:
        return categories[0]

    cats_str = ", ".join(categories)
    prompt = f"""Выбери ОДНУ категорию фонового видео, которая лучше всего подходит для видео на тему "{topic}".
Фон должен быть приятным и не отвлекать от контента.

Доступные категории: {cats_str}

Верни ТОЛЬКО название категории, ничего больше."""

    result = _chat(prompt)
    result = result.strip().strip('"\'').strip().lower()

    # Ищем совпадение
    for cat in categories:
        if cat.lower() == result or cat.lower() in result or result in cat.lower():
            return cat

    # Не нашли — возвращаем первую
    return categories[0]


if __name__ == "__main__":
    print("=== Тест AI модуля (Groq) ===")
    test_title = "Самые странные вещи которые люди находили на Reddit #34"
    print(f"\nНазвание видео: {test_title}")
    topic = generate_topic_from_title(test_title)
    print(f"Сгенерированная тема: {topic}")

    print(f"\n=== Тест генерации метаданных ===")
    meta = generate_video_metadata(test_title, topic)
    print(f"Название: {meta['title']}")
    print(f"Описание: {meta['description']}")
    print(f"Хештеги: {meta['hashtags']}")
    print(f"\nCaption:\n{meta['caption']}")
