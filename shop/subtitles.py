import os
import io
import re
import json
import logging
import tempfile
import threading
import urllib.request
from typing import List, Dict, Any, Optional, Tuple

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection

logger = logging.getLogger(__name__)

# =========================================================
# LANGUAGE REGISTRY
# =========================================================

SUPPORTED_LANGUAGES: Dict[str, Dict[str, str]] = {
    "en": {"code": "en", "name": "English", "native": "English"},
    "ml": {"code": "ml", "name": "Malayalam", "native": "മലയാളം"},
    "hi": {"code": "hi", "name": "Hindi", "native": "हिन्दी"},
    "ta": {"code": "ta", "name": "Tamil", "native": "தமிழ்"},
    "ar": {"code": "ar", "name": "Arabic", "native": "العربية"},
    "fr": {"code": "fr", "name": "French", "native": "Français"},
    "es": {"code": "es", "name": "Spanish", "native": "Español"},
    "de": {"code": "de", "name": "German", "native": "Deutsch"},
    "ja": {"code": "ja", "name": "Japanese", "native": "日本語"},
    "pt": {"code": "pt", "name": "Portuguese", "native": "Português"},
    "ru": {"code": "ru", "name": "Russian", "native": "Русский"},
    "zh": {"code": "zh", "name": "Chinese", "native": "中文"},
    "it": {"code": "it", "name": "Italian", "native": "Italiano"},
    "te": {"code": "te", "name": "Telugu", "native": "తెలుగు"},
    "bn": {"code": "bn", "name": "Bengali", "native": "বাংলা"},
    "ko": {"code": "ko", "name": "Korean", "native": "한국어"},
}

DEFAULT_TARGET_LANGUAGES: List[str] = [
    "en",
    "ml",
    "hi",
    "ta",
    "ar",
    "fr",
    "es",
    "de",
    "ja",
]


def get_language_name(code: str) -> str:
    code_lower = str(code or "").strip().lower()
    if code_lower in SUPPORTED_LANGUAGES:
        return SUPPORTED_LANGUAGES[code_lower]["name"]
    return code_lower.capitalize() or "Unknown"


def get_active_target_languages() -> List[str]:
    """
    Retrieve active target languages configured by admin or default set.
    """
    try:
        from .models import SubtitleSetting
        setting = SubtitleSetting.objects.filter(key="target_languages").first()
        if setting and isinstance(setting.value, list) and len(setting.value) > 0:
            return [str(lang).strip().lower() for lang in setting.value if lang]
    except Exception:
        pass
    return list(DEFAULT_TARGET_LANGUAGES)


# =========================================================
# TIMESTAMP & WEBVTT / SRT UTILITIES
# =========================================================

def format_vtt_timestamp(seconds: float) -> str:
    """Format seconds into WebVTT timestamp: 00:01:23.456"""
    if not isinstance(seconds, (int, float)) or seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds into SRT timestamp: 00:01:23,456"""
    vtt_ts = format_vtt_timestamp(seconds)
    return vtt_ts.replace(".", ",")


def parse_timestamp_to_seconds(ts: str) -> float:
    """
    Parse WebVTT or SRT timestamp (00:01:23.456 or 01:23.456 or 00:01:23,456) to seconds.
    """
    if not ts:
        return 0.0
    clean = str(ts).strip().replace(",", ".")
    parts = clean.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 1:
            return float(parts[0])
    except (ValueError, TypeError):
        return 0.0
    return 0.0


def cues_to_vtt(cues: List[Dict[str, Any]]) -> str:
    """
    Generate WebVTT file content from cue list.
    """
    lines = ["WEBVTT", ""]
    for i, cue in enumerate(cues, start=1):
        start = cue.get("start", 0.0)
        end = cue.get("end", start + 2.0)
        text = str(cue.get("text", "")).strip()
        if not text:
            continue
        start_str = format_vtt_timestamp(start)
        end_str = format_vtt_timestamp(end)
        lines.append(str(cue.get("id", i)))
        lines.append(f"{start_str} --> {end_str}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def cues_to_srt(cues: List[Dict[str, Any]]) -> str:
    """
    Generate SRT file content from cue list.
    """
    lines = []
    for i, cue in enumerate(cues, start=1):
        start = cue.get("start", 0.0)
        end = cue.get("end", start + 2.0)
        text = str(cue.get("text", "")).strip()
        if not text:
            continue
        start_str = format_srt_timestamp(start)
        end_str = format_srt_timestamp(end)
        lines.append(str(cue.get("id", i)))
        lines.append(f"{start_str} --> {end_str}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def vtt_to_cues(vtt_text: str) -> List[Dict[str, Any]]:
    """
    Parse a WebVTT or SRT text into structured cues.
    """
    cues: List[Dict[str, Any]] = []
    if not vtt_text:
        return cues

    lines = [l.strip() for l in vtt_text.splitlines()]
    i = 0
    cue_id = 1

    time_pattern = re.compile(r"((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})\s*-->\s*((?:\d{2}:)?\d{2}:\d{2}[\.,]\d{3})")

    while i < len(lines):
        line = lines[i]
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("STYLE"):
            i += 1
            continue

        match = time_pattern.search(line)
        if match:
            start_str, end_str = match.groups()
            start_sec = parse_timestamp_to_seconds(start_str)
            end_sec = parse_timestamp_to_seconds(end_str)

            text_lines = []
            i += 1
            while i < len(lines) and lines[i] and not time_pattern.search(lines[i]):
                if lines[i].isdigit() and (i + 1 < len(lines)) and time_pattern.search(lines[i + 1]):
                    break
                text_lines.append(lines[i])
                i += 1

            text = " ".join(text_lines).strip()
            if text:
                cues.append({
                    "id": cue_id,
                    "start": round(start_sec, 3),
                    "end": round(end_sec, 3),
                    "start_formatted": format_vtt_timestamp(start_sec),
                    "end_formatted": format_vtt_timestamp(end_sec),
                    "text": text,
                })
                cue_id += 1
            continue

        i += 1

    return cues


# =========================================================
# AI CLIENT & SPEECH-TO-TEXT (GROQ WHISPER)
# =========================================================

def get_groq_api_key() -> str:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key and hasattr(settings, "GROQ_API_KEY"):
        key = str(settings.GROQ_API_KEY).strip()
    return key


def get_ai_client():
    """
    Create OpenAI-compatible client initialized with Groq API credentials.
    """
    api_key = get_groq_api_key()
    if not api_key:
        raise ValueError("GROQ_API_KEY is not configured in environment or settings.")

    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    except ImportError:
        raise ImportError("openai package is required to connect to Groq Speech-to-Text API.")


def resolve_video_file_to_local(video_field, video_url: str = "") -> Tuple[str, bool]:
    """
    Resolve a video field or URL to a local readable file path.
    Returns (file_path, is_temporary).
    """
    if video_field:
        try:
            if hasattr(video_field, "path") and os.path.exists(video_field.path):
                return video_field.path, False
        except Exception:
            pass

        try:
            name = str(getattr(video_field, "name", "")).lstrip("/")
            if default_storage.exists(name):
                try:
                    return default_storage.path(name), False
                except NotImplementedError:
                    pass
        except Exception:
            pass

    if video_field and getattr(video_field, "name", ""):
        candidate = os.path.join(settings.MEDIA_ROOT, video_field.name.lstrip("/"))
        if os.path.exists(candidate):
            return candidate, False
        base_candidate = os.path.join(settings.BASE_DIR, "media", video_field.name.lstrip("/"))
        if os.path.exists(base_candidate):
            return base_candidate, False

    target_url = str(video_url or "").strip()
    if not target_url and video_field:
        try:
            target_url = str(video_field.url or "")
        except Exception:
            pass

    if target_url.startswith(("http://", "https://")):
        suffix = ".mp4"
        if ".webm" in target_url.lower():
            suffix = ".webm"
        elif ".mov" in target_url.lower():
            suffix = ".mov"
        elif ".m4a" in target_url.lower():
            suffix = ".m4a"

        temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        req = urllib.request.Request(
            target_url,
            headers={"User-Agent": "CLOUT-Subtitle-Generator/1.0"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(temp_file.name, "wb") as out_f:
            chunk_size = 1024 * 1024
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out_f.write(chunk)
        return temp_file.name, True

    if getattr(settings, "USE_S3", False) and video_field and getattr(video_field, "name", ""):
        import boto3
        from botocore.config import Config
        client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            region_name=settings.AWS_S3_REGION_NAME,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        temp_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        client.download_file(settings.AWS_STORAGE_BUCKET_NAME, video_field.name, temp_file.name)
        return temp_file.name, True

    raise FileNotFoundError("Could not locate or download the video file for transcription.")


def transcribe_video_audio(local_file_path: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Perform Speech-to-Text transcription on the video file using Groq Whisper.
    Returns (cues, detected_language_name).
    """
    client = get_ai_client()

    file_size_mb = os.path.getsize(local_file_path) / (1024 * 1024)
    logger.info("Transcribing video (%s, %.2f MB) with Whisper...", local_file_path, file_size_mb)

    audio_path_to_send = local_file_path
    temp_audio_created = False

    if file_size_mb > 24:
        try:
            import subprocess
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            temp_audio.close()
            cmd = [
                ffmpeg_exe, "-y", "-i", local_file_path,
                "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k",
                temp_audio.name
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            audio_path_to_send = temp_audio.name
            temp_audio_created = True
            logger.info("Extracted compressed audio (%.2f MB) for Whisper.", os.path.getsize(temp_audio.name) / (1024 * 1024))
        except Exception as exc:
            logger.warning("Could not extract compressed audio with ffmpeg (%s), proceeding with original file.", exc)

    try:
        with open(audio_path_to_send, "rb") as audio_file:
            transcript_response = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file,
                response_format="verbose_json",
            )
    finally:
        if temp_audio_created and os.path.exists(audio_path_to_send):
            try:
                os.remove(audio_path_to_send)
            except Exception:
                pass

    detected_lang = getattr(transcript_response, "language", "") or "english"
    detected_lang = str(detected_lang).strip().lower()

    raw_segments = getattr(transcript_response, "segments", []) or []
    cues: List[Dict[str, Any]] = []

    for i, seg in enumerate(raw_segments, start=1):
        if isinstance(seg, dict):
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", start + 2.0))
            text = str(seg.get("text", "")).strip()
        else:
            start = float(getattr(seg, "start", 0.0))
            end = float(getattr(seg, "end", start + 2.0))
            text = str(getattr(seg, "text", "")).strip()

        if not text:
            continue

        cues.append({
            "id": i,
            "start": round(start, 3),
            "end": round(end, 3),
            "start_formatted": format_vtt_timestamp(start),
            "end_formatted": format_vtt_timestamp(end),
            "text": text,
        })

    if not cues:
        full_text = str(getattr(transcript_response, "text", "")).strip()
        if full_text:
            duration = float(getattr(transcript_response, "duration", 10.0) or 10.0)
            cues.append({
                "id": 1,
                "start": 0.0,
                "end": round(duration, 3),
                "start_formatted": format_vtt_timestamp(0.0),
                "end_formatted": format_vtt_timestamp(duration),
                "text": full_text,
            })

    return cues, detected_lang.capitalize()


# =========================================================
# AI MULTILINGUAL TRANSLATION (GROQ LLM)
# =========================================================

def translate_cues_to_language(
    cues: List[Dict[str, Any]],
    source_language_name: str,
    target_language_code: str,
) -> List[Dict[str, Any]]:
    """
    Translate subtitle cues into the target language preserving cue IDs and timings.
    """
    target_lang_name = get_language_name(target_language_code)
    target_lang_native = SUPPORTED_LANGUAGES.get(target_language_code, {}).get("native", target_lang_name)

    if (
        source_language_name.lower() == target_lang_name.lower()
        or source_language_name.lower().startswith(target_language_code)
    ):
        return [dict(c) for c in cues]

    if not cues:
        return []

    client = get_ai_client()
    translated_cues = []
    chunk_size = 25

    for chunk_start in range(0, len(cues), chunk_size):
        chunk = cues[chunk_start:chunk_start + chunk_size]
        items_payload = [{"id": c["id"], "text": c["text"]} for c in chunk]

        prompt = (
            f"You are a professional video subtitle and media translator. "
            f"Translate the following subtitle cues accurately from {source_language_name} into {target_lang_name} ({target_lang_native}).\n\n"
            f"CRITICAL RULES:\n"
            f"1. Preserve the EXACT same cue 'id' numbers.\n"
            f"2. Translate naturally and accurately for spoken video dialogue.\n"
            f"3. Return ONLY a valid JSON array of objects with keys 'id' and 'text'.\n"
            f"4. Do NOT wrap in markdown explanation, only raw JSON or ```json markdown block.\n\n"
            f"Input Cues:\n{json.dumps(items_payload, ensure_ascii=False, indent=2)}"
        )

        try:
            completion = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[
                    {"role": "system", "content": f"You are an expert subtitle translator specialized in {target_lang_name}."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=2048,
            )
            raw_text = completion.choices[0].message.content.strip()

            cleaned_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
            cleaned_text = re.sub(r"\s*```$", "", cleaned_text, flags=re.MULTILINE).strip()

            translated_items = json.loads(cleaned_text)
            trans_dict = {int(item["id"]): str(item["text"]).strip() for item in translated_items if "id" in item and "text" in item}

            for original_cue in chunk:
                new_cue = dict(original_cue)
                if original_cue["id"] in trans_dict and trans_dict[original_cue["id"]]:
                    new_cue["text"] = trans_dict[original_cue["id"]]
                translated_cues.append(new_cue)

        except Exception as exc:
            logger.warning("LLM translation for chunk failed (%s). Falling back to original cue texts.", exc)
            for original_cue in chunk:
                translated_cues.append(dict(original_cue))

    return translated_cues


# =========================================================
# MAIN GENERATION PIPELINE & ASYNC WORKER
# =========================================================

def process_subtitles_for_lesson(
    lesson_id: int,
    target_languages: Optional[List[str]] = None,
) -> bool:
    """
    Main synchronous function to generate multilingual subtitles for a Lesson.
    1. Extracts audio & calls Whisper for transcript + language detection.
    2. Translates into each target language.
    3. Generates WebVTT & SRT files and saves to database.
    """
    from .models import Lesson, SubtitleTrack

    connection.close()

    lesson = Lesson.objects.filter(id=lesson_id).first()
    if not lesson or (not lesson.video and not lesson.video_url):
        logger.warning("Lesson %s does not exist or has no video attached.", lesson_id)
        return False

    lesson.subtitle_status = "processing"
    lesson.subtitle_error = ""
    lesson.save(update_fields=["subtitle_status", "subtitle_error"])

    temp_file_path = None
    is_temp = False

    try:
        local_path, is_temp = resolve_video_file_to_local(lesson.video, lesson.video_url)
        temp_file_path = local_path if is_temp else None

        cues, detected_language_name = transcribe_video_audio(local_path)

        if not cues:
            raise ValueError("No speech or audio transcript could be generated from the video.")

        detected_lang_code = "en"
        for code, info in SUPPORTED_LANGUAGES.items():
            if (
                info["name"].lower() == detected_language_name.lower()
                or detected_language_name.lower().startswith(code)
            ):
                detected_lang_code = code
                break

        lesson.detected_language = detected_language_name
        lesson.detected_language_code = detected_lang_code
        lesson.save(update_fields=["detected_language", "detected_language_code"])

        languages_to_generate = target_languages or get_active_target_languages()
        if detected_lang_code not in languages_to_generate:
            languages_to_generate = [detected_lang_code] + [l for l in languages_to_generate if l != detected_lang_code]

        logger.info(
            "Generating subtitles for Lesson %s (Detected: %s/%s) in %d languages: %s",
            lesson.id, detected_language_name, detected_lang_code, len(languages_to_generate), languages_to_generate
        )

        for lang_code in languages_to_generate:
            lang_code = str(lang_code).strip().lower()
            lang_name = get_language_name(lang_code)
            is_orig = (lang_code == detected_lang_code)

            try:
                if is_orig:
                    lang_cues = [dict(c) for c in cues]
                else:
                    lang_cues = translate_cues_to_language(cues, detected_language_name, lang_code)

                vtt_text = cues_to_vtt(lang_cues)
                srt_text = cues_to_srt(lang_cues)

                sub_track, _ = SubtitleTrack.objects.get_or_create(
                    lesson=lesson,
                    language_code=lang_code,
                    defaults={
                        "language_name": lang_name,
                        "is_original": is_orig,
                        "status": "ready",
                    }
                )

                sub_track.language_name = lang_name
                sub_track.is_original = is_orig
                sub_track.cues_data = lang_cues
                sub_track.vtt_content = vtt_text
                sub_track.srt_content = srt_text
                sub_track.status = "ready"
                sub_track.error_message = ""

                vtt_filename = f"subtitles/lesson_{lesson.id}_{lang_code}.vtt"
                srt_filename = f"subtitles/lesson_{lesson.id}_{lang_code}.srt"

                sub_track.vtt_file.save(vtt_filename, ContentFile(vtt_text.encode("utf-8")), save=False)
                sub_track.srt_file.save(srt_filename, ContentFile(srt_text.encode("utf-8")), save=False)
                sub_track.save()

                logger.info("Saved %s subtitles (%d cues) for Lesson %s.", lang_name, len(lang_cues), lesson.id)

            except Exception as lang_exc:
                logger.exception("Failed generating subtitles for language %s on lesson %s: %s", lang_code, lesson.id, lang_exc)
                sub_track, _ = SubtitleTrack.objects.get_or_create(
                    lesson=lesson,
                    language_code=lang_code,
                    defaults={"language_name": lang_name, "status": "failed"}
                )
                sub_track.status = "failed"
                sub_track.error_message = str(lang_exc)
                sub_track.save()

        lesson.subtitle_status = "ready"
        lesson.subtitle_error = ""
        lesson.save(update_fields=["subtitle_status", "subtitle_error"])
        return True

    except Exception as exc:
        logger.exception("Subtitle generation failed for Lesson %s: %s", lesson_id, exc)
        lesson.subtitle_status = "failed"
        lesson.subtitle_error = str(exc)
        lesson.save(update_fields=["subtitle_status", "subtitle_error"])
        return False

    finally:
        if is_temp and temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception:
                pass
        connection.close()


def trigger_auto_subtitle_generation(
    lesson_id: int,
    target_languages: Optional[List[str]] = None,
) -> threading.Thread:
    """
    Trigger subtitle generation in a safe background thread.
    Non-blocking: returns immediately so video uploads complete without delay.
    """
    worker_thread = threading.Thread(
        target=process_subtitles_for_lesson,
        args=(lesson_id, target_languages),
        daemon=True,
        name=f"SubtitlesWorker-Lesson-{lesson_id}"
    )
    worker_thread.start()
    return worker_thread
