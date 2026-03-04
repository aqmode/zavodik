# 🎬 TikTok Content Generator

Автоматическая генерация TikTok контента из видео YouTube канала @upvotemedia.

## 🏗️ Архитектура

```
.env                        ← API ключи, тема, настройки
main.py                     ← Главный скрипт (оркестрация)
gemini_module.py            ← Gemini API: запросы, хештеги, анализ
youtube_search.py           ← YouTube Data API: поиск на канале
downloader.py               ← yt-dlp: скачивание аудио и фона
video_processor.py          ← ffmpeg + Whisper: обработка видео

backgrounds/                ← Категории фоновых видео
├── car_washing.txt         ← Ссылки на TikTok видео (car washing)
├── glass_cleaning.txt      ← Ссылки на TikTok видео (glass cleaning)
├── carpet_cleaning.txt     ← Ссылки на TikTok видео (carpet cleaning)
└── README.txt              ← Инструкция по добавлению категорий

youtube_links.txt           ← Ссылки с YouTube (заполняется автоматически)
hashtags.txt                ← Хештеги (генерируются Gemini)

downloads/                  ← Скачанные файлы
├── audio/                  ← Аудио с YouTube
└── background/             ← Фоновые видео по категориям
    ├── car_washing/
    ├── glass_cleaning/
    └── carpet_cleaning/

output/                     ← Готовые клипы (по 30 сек)
```

## 📦 Установка

```bash
pip install -r requirements.txt
```

Также необходимы:
- **ffmpeg** (уже установлен)
- **yt-dlp** (устанавливается через pip)

## ⚙️ Настройка

Отредактируй `.env`:

```env
GEMINI_API_KEY=...              # Gemini API ключ
YOUTUBE_API_KEY=...             # YouTube Data API v3 ключ
YOUTUBE_CHANNEL_URL=https://www.youtube.com/@upvotemedia
CONTENT_TOPIC=Интересные факты  # Тема для поиска
CLIP_DURATION=30                # Длительность клипов (сек)
MAX_YOUTUBE_VIDEOS=5            # Макс. видео для скачивания
```

## 📂 Категории фона

Фоновые видео разделены по категориям. Каждый `.txt` файл в `backgrounds/` = категория.

### Добавить новую категорию:
1. Создай файл `backgrounds/название_категории.txt`
2. Добавь ссылки на TikTok видео (по одной на строку)
3. Скрипт автоматически подхватит!

### Ротация категорий:
- Видео 1 → `car_washing`
- Видео 2 → `carpet_cleaning`
- Видео 3 → `glass_cleaning`
- Видео 4 → `car_washing` (по кругу)
- ...

## 🚀 Использование

### Полный пайплайн:
```bash
python main.py
```

### Пошагово:
```bash
python main.py --search-only      # Только поиск видео
python main.py --download-only    # Только скачивание
python main.py --process-only     # Только обработка
```

### Опции:
```bash
python main.py --no-subtitles     # Без субтитров
python main.py --no-search        # Пропустить поиск (использовать существующие ссылки)
```

## 🔄 Пайплайн

1. **Gemini API** генерирует поисковые запросы и хештеги по теме
2. **YouTube Data API** ищет видео **ТОЛЬКО на канале @upvotemedia**
3. **Gemini API** выбирает самые интересные видео
4. **yt-dlp** скачивает аудио с YouTube и фоновые видео из TikTok
5. **Whisper** генерирует субтитры (русский язык)
6. **ffmpeg** склеивает:
   - Фоновое видео (без звука, 9:16)
   - Аудио с YouTube
   - Субтитры (burn-in)
   - Если аудио длиннее фона → фон продлевается следующим видео
7. **ffmpeg** нарезает на клипы по 30 секунд
8. Готовые клипы → папка `output/`

## 🤖 Роль Gemini API

- **Генерация запросов**: по теме из `.env` создаёт разнообразные поисковые запросы
- **Генерация хештегов**: создаёт трендовые хештеги для TikTok (рус + англ)
- **Выбор видео**: анализирует найденные видео и выбирает самые вирусные
- **Коррекция субтитров**: исправляет ошибки распознавания речи
