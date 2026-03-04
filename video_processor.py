"""
Модуль обработки видео.
- Генерация субтитров через faster-whisper
- Нарезка аудио на чанки по N секунд
- Параллельная сборка клипов (asyncio + ffmpeg)
- Наложение субтитров (burn-in)
"""

import asyncio
import subprocess
import os
import json
import math
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
CLIP_DURATION = int(os.getenv("CLIP_DURATION", "60"))
CLIPS_PER_VIDEO = int(os.getenv("CLIPS_PER_VIDEO", "3"))
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "logs")
TEMP_DIR = os.path.join(DOWNLOAD_DIR, "temp")

MAX_PARALLEL_FFMPEG = 3


def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)


def get_media_duration(filepath):
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        filepath,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception as e:
        print(f"  Ошибка длительности {filepath}: {e}")
        return 0.0


def get_video_resolution(filepath):
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-select_streams", "v:0",
        filepath,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)
        stream = info["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception:
        return 720, 1280


def _ass_timestamp(seconds):
    """Форматирует секунды в ASS timestamp: H:MM:SS.cc"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# Путь к шрифту в корне проекта
FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eitai.otf")


def _build_ass_header():
    """ASS заголовок: шрифт eitai, белый текст с чёрной обводкой."""
    font_name = "eitai"
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 720\n"
        "PlayResY: 1280\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},56,&H00FFFFFF,&H000000FF,"
        "&H00000000,&H00000000,"
        "0,0,0,0,"
        "100,100,0,0,"
        "1,3,1,"
        "2,20,20,260,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def generate_subtitles(audio_path, output_ass):
    """
    Генерирует ASS субтитры:
    - faster-whisper, word_timestamps
    - Группы по 1-2 слова
    - Шрифт eitai.otf, белый текст с чёрной обводкой 3px
    - Без анимаций
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  faster-whisper не установлен!")
        return None

    print(f"  🎤 Субтитры: {os.path.basename(audio_path)}")

    try:
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments_gen, info = model.transcribe(
            audio_path,
            language="ru",
            beam_size=5,
            word_timestamps=True,
        )

        # Собираем все слова с таймингами
        all_words = []
        for seg in segments_gen:
            if seg.words:
                for w in seg.words:
                    word = w.word.strip()
                    if word:
                        all_words.append((w.start, w.end, word))

        if not all_words:
            print("  ⚠ Нет распознанных слов.")
            return None

        # Группируем по 2 слова
        MAX_WORDS = 2
        groups = []
        i = 0
        while i < len(all_words):
            end_i = min(i + MAX_WORDS, len(all_words))
            chunk = all_words[i:end_i]
            g_start = chunk[0][0]
            g_end = chunk[-1][1]
            text = " ".join(w[2].upper() for w in chunk)
            groups.append((g_start, g_end, text))
            i = end_i

        # Генерируем ASS
        ass_content = _build_ass_header()
        for g_start, g_end, text in groups:
            ts = _ass_timestamp(g_start)
            te = _ass_timestamp(g_end)
            ass_content += f"Dialogue: 0,{ts},{te},Default,,0,0,0,,{text}\n"

        with open(output_ass, "w", encoding="utf-8") as f:
            f.write(ass_content)

        print(f"  ✓ Субтитры: {output_ass} ({len(groups)} фраз)")
        return output_ass

    except Exception as e:
        print(f"  ✗ Ошибка субтитров: {e}")
        return None


def split_audio_into_chunks(audio_path, chunk_duration=None):
    """Нарезает аудио на куски по N секунд через ffmpeg (-c copy)."""
    ensure_dirs()

    if chunk_duration is None:
        chunk_duration = CLIP_DURATION

    total_duration = get_media_duration(audio_path)
    if total_duration <= 0:
        return []

    num_chunks = math.ceil(total_duration / chunk_duration)
    # Ограничиваем количество чанков до CLIPS_PER_VIDEO
    num_chunks = min(num_chunks, CLIPS_PER_VIDEO)
    chunks = []

    for i in range(num_chunks):
        start_time = i * chunk_duration
        chunk_path = os.path.join(TEMP_DIR, f"audio_chunk_{i:03d}.mp3")

        cmd = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-ss", str(start_time),
            "-t", str(chunk_duration),
            "-c", "copy",
            chunk_path,
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=60,
            )
            if proc.returncode == 0 and os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
                chunks.append(chunk_path)
            else:
                break
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            break

    print(f"  Аудио нарезано на {len(chunks)} частей по {chunk_duration} сек")
    return chunks


async def _run_ffmpeg_async(cmd, label="", timeout=600):
    """Запускает ffmpeg асинхронно."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        _, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            err_text = stderr_data.decode(errors="replace")[-500:] if stderr_data else ""
            print(f"  ffmpeg error [{label}] (code {proc.returncode}): {err_text}")
        return proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        print(f"  ffmpeg timeout [{label}] ({timeout} сек)")
        return -1


async def make_clip_async(audio_chunk, srt_path, bg_videos, output_path,
                          clip_index, total_clips, bg_offset=0, semaphore=None,
                          category_name=""):
    """Асинхронно создаёт один клип: аудио-чанк + фон + субтитры."""
    sem = semaphore or asyncio.Semaphore(MAX_PARALLEL_FFMPEG)

    async with sem:
        label = f"{clip_index + 1}/{total_clips}"
        cat_label = f" [{category_name}]" if category_name else ""
        print(f"  [{label}]{cat_label} Начинаю сборку...", flush=True)

        chunk_duration = get_media_duration(audio_chunk)
        if chunk_duration <= 0:
            return None

        bg_video = bg_videos[bg_offset % len(bg_videos)]
        bg_duration = get_media_duration(bg_video)

        input_args = []
        if bg_duration > 0 and bg_duration < chunk_duration:
            loop_count = math.ceil(chunk_duration / bg_duration)
            input_args = ["-stream_loop", str(loop_count)]

        vf_parts = [
            "scale=720:1280:force_original_aspect_ratio=decrease",
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
        ]

        if srt_path and os.path.exists(srt_path) and os.path.getsize(srt_path) > 10:
            srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
            fonts_dir = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/").replace(":", "\\:")
            vf_parts.append(f"ass='{srt_escaped}':fontsdir='{fonts_dir}'")

        vf_str = ",".join(vf_parts)

        cmd = [
            "ffmpeg", "-y",
            *input_args,
            "-i", bg_video,
            "-i", audio_chunk,
            "-t", str(chunk_duration),
            "-vf", vf_str,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest",
            "-r", "30",
            output_path,
        ]

        rc = await _run_ffmpeg_async(cmd, label=label, timeout=600)
        if rc == 0 and os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            print(f"  [{label}] {os.path.basename(output_path)} ({size_mb:.1f} MB)")
            return output_path
        return None


async def process_single_video_async(audio_path, bg_videos, video_index=0,
                                     category="", add_subtitles=True,
                                     bg_by_category=None):
    """
    Полный пайплайн (async):
    1. Нарезка аудио на 60-сек чанки
    2. Генерация ASS-субтитров для каждого чанка
    3. Параллельная сборка клипов
    """
    ensure_dirs()

    print(f"\n{'='*50}")
    print(f"Обработка видео #{video_index + 1}")
    if category:
        print(f"Категория фона: {category}")
    print(f"{'='*50}")

    # Строим словарь категорий для ротации по клипам
    # bg_by_category: {name: [paths]} — если передан, используем ротацию
    # иначе — bg_videos для всех клипов
    if bg_by_category and len(bg_by_category) > 1:
        cat_names = sorted(bg_by_category.keys())
        print(f"  Ротация фонов по клипам: {cat_names}")
    else:
        cat_names = None

    audio_duration = get_media_duration(audio_path)
    print(f"  Длительность аудио: {audio_duration:.1f} сек")

    # Обрезаем аудио до CLIPS_PER_VIDEO * CLIP_DURATION
    max_audio = CLIPS_PER_VIDEO * CLIP_DURATION
    trimmed_audio = audio_path
    if audio_duration > max_audio:
        trimmed_path = os.path.join(TEMP_DIR, "audio_trimmed.mp3")
        trim_cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-t", str(max_audio),
            "-c", "copy", trimmed_path,
        ]
        subprocess.run(trim_cmd, capture_output=True)
        if os.path.exists(trimmed_path):
            trimmed_audio = trimmed_path
            print(f"  Обрезано до {max_audio} сек ({CLIPS_PER_VIDEO} клипа)")

    # 1. Нарезаем аудио
    print(f"  Нарезаю аудио по {CLIP_DURATION} сек...")
    audio_chunks = split_audio_into_chunks(trimmed_audio, CLIP_DURATION)
    if not audio_chunks:
        print("  Не удалось нарезать аудио!")
        return []

    total = len(audio_chunks)

    # 2. Генерируем ASS-субтитры для каждого чанка
    chunk_subs = []
    if add_subtitles:
        print(f"  Генерирую субтитры для {total} чанков...")
        for i, chunk_audio in enumerate(audio_chunks):
            ass_path = os.path.join(TEMP_DIR, f"sub_chunk_{i:03d}.ass")
            result = generate_subtitles(chunk_audio, ass_path)
            if result and os.path.exists(result) and os.path.getsize(result) > 10:
                chunk_subs.append(result)
            else:
                chunk_subs.append(None)
    else:
        chunk_subs = [None] * total

    # 3. Параллельная сборка клипов
    print(f"  Создаю {total} клипов (до {MAX_PARALLEL_FFMPEG} параллельно)...")

    sem = asyncio.Semaphore(MAX_PARALLEL_FFMPEG)
    tasks = []

    for i in range(total):
        clip_path = os.path.join(OUTPUT_DIR, f"video_{video_index:03d}_part_{i+1:03d}.mp4")

        # Ротация категорий: каждый клип — новая категория
        if cat_names:
            clip_cat = cat_names[i % len(cat_names)]
            clip_bg_videos = bg_by_category[clip_cat]
        else:
            clip_cat = category
            clip_bg_videos = bg_videos

        task = make_clip_async(
            audio_chunk=audio_chunks[i],
            srt_path=chunk_subs[i],
            bg_videos=clip_bg_videos,
            output_path=clip_path,
            clip_index=i,
            total_clips=total,
            bg_offset=0,
            semaphore=sem,
            category_name=clip_cat,
        )
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    # 5. Результаты
    clips = []
    for result in results:
        if result and os.path.exists(result):
            clips.append(result)

    # 6. Чистим временные файлы
    if trimmed_audio != audio_path:
        try:
            os.remove(trimmed_audio)
        except OSError:
            pass
    for chunk in audio_chunks:
        try:
            os.remove(chunk)
        except OSError:
            pass
    for sub in chunk_subs:
        if sub:
            try:
                os.remove(sub)
            except OSError:
                pass

    print(f"\n{'='*50}")
    print(f"Создано {len(clips)}/{total} клипов")
    for clip in clips:
        print(f"  {clip}")

    return clips


def process_single_video(audio_path, bg_videos, video_index=0,
                         category="", add_subtitles=True,
                         bg_by_category=None):
    """Синхронная обёртка для process_single_video_async."""
    return asyncio.run(
        process_single_video_async(
            audio_path=audio_path,
            bg_videos=bg_videos,
            video_index=video_index,
            category=category,
            add_subtitles=add_subtitles,
            bg_by_category=bg_by_category,
        )
    )


if __name__ == "__main__":
    print("=== Тест модуля обработки видео ===")
    print(f"Длительность клипа: {CLIP_DURATION} сек")
    print(f"Папка вывода: {OUTPUT_DIR}")
    print(f"Параллельных ffmpeg: {MAX_PARALLEL_FFMPEG}")
