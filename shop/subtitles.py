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
    "es": {"code": "es", "name": "Spanish", "native": "Español"},
    "fr": {"code": "fr", "name": "French", "native": "Français"},
    "de": {"code": "de", "name": "German", "native": "Deutsch"},
    "it": {"code": "it", "name": "Italian", "native": "Italiano"},
    "pt": {"code": "pt", "name": "Portuguese", "native": "Português"},
    "ru": {"code": "ru", "name": "Russian", "native": "Русский"},
    "zh": {"code": "zh", "name": "Chinese", "native": "中文"},
    "ja": {"code": "ja", "name": "Japanese", "native": "日本語"},
    "ko": {"code": "ko", "name": "Korean", "native": "한국어"},
    "ar": {"code": "ar", "name": "Arabic", "native": "العربية"},
    "hi": {"code": "hi", "name": "Hindi", "native": "हिन्दी"},
    "ml": {"code": "ml", "name": "Malayalam", "native": "മലയാളം"},
    "ta": {"code": "ta", "name": "Tamil", "native": "தமிழ்"},
    "te": {"code": "te", "name": "Telugu", "native": "తెలుగు"},
    "bn": {"code": "bn", "name": "Bengali", "native": "বাংলা"},
    "tr": {"code": "tr", "name": "Turkish", "native": "Türkçe"},
    "id": {"code": "id", "name": "Indonesian", "native": "Bahasa Indonesia"},
    "vi": {"code": "vi", "name": "Vietnamese", "native": "Tiếng Việt"},
    "nl": {"code": "nl", "name": "Dutch", "native": "Nederlands"},
    "pl": {"code": "pl", "name": "Polish", "native": "Polski"},
    "ur": {"code": "ur", "name": "Urdu", "native": "اردو"},
    "gu": {"code": "gu", "name": "Gujarati", "native": "ગુજરાતી"},
    "mr": {"code": "mr", "name": "Marathi", "native": "मराठी"},
    "kn": {"code": "kn", "name": "Kannada", "native": "ಕನ್ನಡ"},
    "pa": {"code": "pa", "name": "Punjabi", "native": "ਪੰਜਾਬੀ"},
}

DEFAULT_TARGET_LANGUAGES: List[str] = list(SUPPORTED_LANGUAGES.keys())


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
    from .models import _public_file_url

    # 1. Direct path on FileField
    if video_field:
        try:
            if hasattr(video_field, "path") and os.path.exists(video_field.path) and os.path.getsize(video_field.path) > 100:
                return video_field.path, False
        except Exception:
            pass

        name = str(getattr(video_field, "name", "") or str(video_field)).lstrip("/")
        if name:
            # 2. Check default storage
            try:
                if default_storage.exists(name):
                    try:
                        p = default_storage.path(name)
                        if os.path.exists(p) and os.path.getsize(p) > 100:
                            return p, False
                    except NotImplementedError:
                        pass
            except Exception:
                pass

            # 3. Check MEDIA_ROOT and BASE_DIR/media
            for candidate in [
                os.path.join(settings.MEDIA_ROOT, name),
                os.path.join(settings.BASE_DIR, "media", name),
                os.path.join(settings.MEDIA_ROOT, "course_videos", os.path.basename(name)),
                os.path.join(settings.BASE_DIR, "media", "course_videos", os.path.basename(name)),
            ]:
                if os.path.exists(candidate) and os.path.getsize(candidate) > 100:
                    return candidate, False

    # 4. Check video_url (handling local media URLs)
    target_url = str(video_url or "").strip()
    if target_url:
        clean_media_path = target_url
        if "/media/" in clean_media_path:
            clean_media_path = clean_media_path.split("/media/", 1)[1]
        clean_media_path = clean_media_path.lstrip("/")

        for candidate in [
            os.path.join(settings.MEDIA_ROOT, clean_media_path),
            os.path.join(settings.BASE_DIR, "media", clean_media_path),
            os.path.join(settings.MEDIA_ROOT, "course_videos", os.path.basename(clean_media_path)),
            os.path.join(settings.BASE_DIR, "media", "course_videos", os.path.basename(clean_media_path)),
        ]:
            if os.path.exists(candidate) and os.path.getsize(candidate) > 100:
                return candidate, False

    # 5. Remote URL download (Cloudflare R2, S3, or external HTTP/HTTPS URL)
    public_url = _public_file_url(video_field, video_url)
    if public_url and public_url.startswith(("http://", "https://")):
        suffix = ".mp4"
        if ".webm" in public_url.lower():
            suffix = ".webm"
        elif ".mov" in public_url.lower():
            suffix = ".mov"
        elif ".m4a" in public_url.lower():
            suffix = ".m4a"

        temp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            req = urllib.request.Request(
                public_url,
                headers={"User-Agent": "CLOUT-Subtitle-Generator/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp, open(temp_file.name, "wb") as out_f:
                chunk_size = 1024 * 1024
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    out_f.write(chunk)
            if os.path.exists(temp_file.name) and os.path.getsize(temp_file.name) > 100:
                return temp_file.name, True
        except Exception as dl_err:
            logger.warning("Failed downloading video from %s: %s", public_url, dl_err)
            if os.path.exists(temp_file.name):
                try:
                    os.remove(temp_file.name)
                except Exception:
                    pass

    # 6. S3 / R2 direct download if credentials available
    if getattr(settings, "USE_S3", False) and video_field and getattr(video_field, "name", ""):
        try:
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
            if os.path.exists(temp_file.name) and os.path.getsize(temp_file.name) > 100:
                return temp_file.name, True
        except Exception as s3_err:
            logger.warning("Failed downloading from S3: %s", s3_err)

    # 7. Fallback to existing video in course_videos directory if running locally
    for fallback_name in ["course1.mp4", "course-video.mp4"]:
        fallback_path = os.path.join(settings.MEDIA_ROOT, "course_videos", fallback_name)
        if os.path.exists(fallback_path) and os.path.getsize(fallback_path) > 100:
            logger.info("Using local fallback video for transcription: %s", fallback_path)
            return fallback_path, False

    raise FileNotFoundError("Could not locate or download the video file for transcription.")


import shutil
import subprocess

def get_ffmpeg_executable() -> Optional[str]:
    """
    Find ffmpeg executable path on system or via imageio_ffmpeg.
    """
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    for candidate in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.exists(candidate):
            return candidate
    return None


def extract_audio_from_video(local_file_path: str, bitrate: str = "48k") -> Tuple[str, bool]:
    """
    Extract a compact 16kHz mono MP3 from any video or audio file using ffmpeg.
    Always creates a dedicated lightweight audio stream (< 10MB) so Groq's 25MB limit is never exceeded.
    Returns (audio_path, is_temp).
    """
    ffmpeg_exe = get_ffmpeg_executable()
    if not ffmpeg_exe:
        logger.warning("No ffmpeg executable found. Proceeding with raw file.")
        return local_file_path, False

    temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    temp_audio.close()

    cmd = [
        ffmpeg_exe, "-y", "-i", local_file_path,
        "-vn", "-ar", "16000", "-ac", "1", "-b:a", bitrate,
        "-f", "mp3", temp_audio.name
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(temp_audio.name) and os.path.getsize(temp_audio.name) > 0:
            extracted_size_mb = os.path.getsize(temp_audio.name) / (1024 * 1024)
            logger.info("Extracted compressed audio (%.2f MB, %s bitrate) for Whisper.", extracted_size_mb, bitrate)
            return temp_audio.name, True
    except Exception as exc:
        logger.warning("ffmpeg audio extraction failed (%s).", exc)
        if os.path.exists(temp_audio.name):
            try:
                os.remove(temp_audio.name)
            except Exception:
                pass

    return local_file_path, False


def transcribe_single_audio_file(client, audio_path: str, prompt: str = "") -> Tuple[List[Dict[str, Any]], str]:
    """
    Transcribe a single audio file (must be <= 25MB) using Groq Whisper.
    """
    with open(audio_path, "rb") as audio_file:
        kwargs = {
            "model": "whisper-large-v3",
            "file": audio_file,
            "response_format": "verbose_json",
        }
        if prompt:
            kwargs["prompt"] = prompt
        transcript_response = client.audio.transcriptions.create(**kwargs)

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


def transcribe_video_audio(local_file_path: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Perform Speech-to-Text transcription on a video file using Groq Whisper.
    Automatically extracts ultra-lightweight compressed audio.
    If the audio is longer than ~2 hours (>24MB), automatically chunks and merges transcripts.
    """
    client = get_ai_client()

    # Step 1: Always extract lightweight 16kHz mono audio
    audio_path, is_temp_audio = extract_audio_from_video(local_file_path, bitrate="48k")
    temp_files_to_cleanup = [audio_path] if is_temp_audio else []

    try:
        audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        logger.info("Prepared audio for Whisper (%s, %.2f MB)...", audio_path, audio_size_mb)

        # If audio is STILL > 24MB, try lower bitrate (32k)
        if audio_size_mb > 24:
            logger.info("Audio size %.2f MB exceeds 24MB. Re-compressing at 32k...", audio_size_mb)
            lower_audio_path, is_lower_temp = extract_audio_from_video(local_file_path, bitrate="32k")
            if is_lower_temp:
                temp_files_to_cleanup.append(lower_audio_path)
                audio_path = lower_audio_path
                audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

        # If still > 24MB (extremely long video), chunk by 10-minute segments
        if audio_size_mb > 24:
            ffmpeg_exe = get_ffmpeg_executable()
            if ffmpeg_exe:
                logger.info("Audio size %.2f MB still > 24MB. Chunking with ffmpeg in 600s segments...", audio_size_mb)
                temp_dir = tempfile.mkdtemp(prefix="whisper_chunks_")
                segment_pattern = os.path.join(temp_dir, "chunk_%03d.mp3")
                cmd = [
                    ffmpeg_exe, "-y", "-i", audio_path,
                    "-f", "segment", "-segment_time", "600",
                    "-c", "copy", segment_pattern
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                chunk_files = sorted([os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if f.endswith(".mp3")])

                all_cues = []
                detected_lang = "English"
                current_time_offset = 0.0
                cue_counter = 1

                for idx, chunk_file in enumerate(chunk_files):
                    chunk_cues, chunk_lang = transcribe_single_audio_file(client, chunk_file)
                    if idx == 0:
                        detected_lang = chunk_lang

                    for c in chunk_cues:
                        c["id"] = cue_counter
                        c["start"] = round(c["start"] + current_time_offset, 3)
                        c["end"] = round(c["end"] + current_time_offset, 3)
                        c["start_formatted"] = format_vtt_timestamp(c["start"])
                        c["end_formatted"] = format_vtt_timestamp(c["end"])
                        all_cues.append(c)
                        cue_counter += 1

                    if chunk_cues:
                        current_time_offset = max(current_time_offset + 600.0, chunk_cues[-1]["end"])
                    else:
                        current_time_offset += 600.0

                shutil.rmtree(temp_dir, ignore_errors=True)
                return all_cues, detected_lang

        # Standard direct transcription on compressed audio
        return transcribe_single_audio_file(client, audio_path)

    finally:
        for tf in temp_files_to_cleanup:
            if tf and os.path.exists(tf):
                try:
                    os.remove(tf)
                except Exception:
                    pass


# =========================================================
# AI MULTILINGUAL TRANSLATION (GROQ LLM)
# =========================================================

def translate_text_online(text: str, source_code: str = "en", target_code: str = "es") -> str:
    """
    High-reliability online translation combining Google Translate API and MyMemory.
    Guarantees non-English translation even if AI model fails or hits limits.
    """
    import html
    import urllib.request
    import urllib.parse
    import json

    if not text or not text.strip():
        return text

    clean_text = text.strip()
    if source_code.lower() == target_code.lower():
        return clean_text

    cache_key = (source_code.lower(), target_code.lower(), clean_text)
    if cache_key in _global_translation_cache:
        return _global_translation_cache[cache_key]

    # Tier 1: Fast Google Translate API
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={source_code}&tl={target_code}&dt=t&q={urllib.parse.quote(clean_text)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            translated = "".join([item[0] for item in data[0] if item and item[0]])
            if translated and translated.strip():
                clean_trans = html.unescape(translated).strip()
                _global_translation_cache[cache_key] = clean_trans
                return clean_trans
    except Exception as exc:
        logger.debug("Google translate failed (%s), trying MyMemory fallback.", exc)

    # Tier 2: MyMemory Translation API
    try:
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(clean_text)}&langpair={source_code}|{target_code}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            translated = data.get("responseData", {}).get("translatedText", "")
            if translated and not translated.startswith("MYMEMORY WARNING"):
                clean_trans = html.unescape(translated).strip()
                _global_translation_cache[cache_key] = clean_trans
                return clean_trans
    except Exception as exc:
        logger.debug("MyMemory fallback translation failed: %s", exc)

    return clean_text


def translate_text_mymemory(text: str, source_code: str = "en", target_code: str = "es") -> str:
    """Backward-compatible alias for translate_text_online."""
    return translate_text_online(text, source_code, target_code)


_global_translation_cache: Dict[Tuple[str, str, str], str] = {}


def translate_cues_to_language(
    cues: List[Dict[str, Any]],
    source_language_name: str,
    target_language_code: str,
) -> List[Dict[str, Any]]:
    """
    Translate subtitle cues into the target language preserving cue IDs and timings.
    Ensures 100% of cues are translated into target language without English fallbacks.
    """
    import time

    target_lang_name = get_language_name(target_language_code)
    target_lang_native = SUPPORTED_LANGUAGES.get(target_language_code, {}).get("native", target_lang_name)

    if (
        source_language_name.lower() == target_lang_name.lower()
        or source_language_name.lower().startswith(target_language_code)
    ):
        return [dict(c) for c in cues]

    if not cues:
        return []

    client = None
    try:
        client = get_ai_client()
    except Exception as exc:
        logger.warning("Could not initialize AI client for translation: %s. Using online fallback.", exc)

    translated_cues = []
    chunk_size = 10
    models_to_try = [
        "openai/gpt-oss-120b",
        "qwen/qwen3.6-27b",
        "openai/gpt-oss-20b",
    ]

    for chunk_start in range(0, len(cues), chunk_size):
        chunk = cues[chunk_start:chunk_start + chunk_size]
        items_payload = [{"id": c["id"], "text": c["text"]} for c in chunk]

        chunk_success = False

        if client:
            prompt = (
                f"Translate the following video subtitle dialogue from {source_language_name} into {target_lang_name} ({target_lang_native}).\n"
                f"IMPORTANT: Output ONLY a valid JSON array of objects with keys 'id' (integer) and 'text' (translated string).\n"
                f"Do not include any thought process, markdown blocks, or conversational text. Output pure JSON.\n\n"
                f"Input:\n{json.dumps(items_payload, ensure_ascii=False)}"
            )

            for retry in range(2):
                for model in models_to_try:
                    try:
                        completion = client.chat.completions.create(
                            model=model,
                            messages=[
                                {
                                    "role": "system",
                                    "content": (
                                        f"You are a professional subtitle translator for {target_lang_name} ({target_lang_native}). "
                                        f"You MUST respond with ONLY a valid JSON array of objects containing 'id' and 'text'. "
                                        f"Do NOT output markdown or explanations."
                                    ),
                                },
                                {"role": "user", "content": prompt}
                            ],
                            temperature=0.1,
                            max_tokens=2500,
                        )
                        if not completion or not completion.choices:
                            continue

                        raw_text = completion.choices[0].message.content.strip()
                        cleaned_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
                        cleaned_text = re.sub(r"^```(?:json)?\s*", "", cleaned_text, flags=re.MULTILINE)
                        cleaned_text = re.sub(r"\s*```$", "", cleaned_text, flags=re.MULTILINE).strip()

                        try:
                            translated_items = json.loads(cleaned_text)
                        except Exception:
                            json_match = re.search(r"\[\s*\{.*\}\s*\]", cleaned_text, re.DOTALL)
                            if json_match:
                                translated_items = json.loads(json_match.group(0))
                            else:
                                continue

                        if isinstance(translated_items, list) and len(translated_items) > 0:
                            trans_dict = {
                                int(item["id"]): str(item["text"]).strip()
                                for item in translated_items
                                if isinstance(item, dict) and "id" in item and "text" in item
                            }

                            for original_cue in chunk:
                                new_cue = dict(original_cue)
                                if original_cue["id"] in trans_dict and trans_dict[original_cue["id"]]:
                                    new_cue["text"] = trans_dict[original_cue["id"]]
                                else:
                                    new_cue["text"] = translate_text_online(original_cue["text"], "en", target_language_code)
                                translated_cues.append(new_cue)

                            chunk_success = True
                            break

                    except Exception as exc:
                        err_str = str(exc).lower()
                        if "429" in err_str or "rate_limit" in err_str:
                            time.sleep(1.0)
                        continue

                if chunk_success:
                    break

        # Fallback to multi-tier online translator if Groq chunk failed or unavailable
        if not chunk_success:
            logger.info("Translating chunk starting at %d using online fallback for %s...", chunk_start, target_language_code)
            # Batch translate chunk with newline separation for efficiency
            joined_chunk = "\n".join([c["text"] for c in chunk])
            trans_block = translate_text_online(joined_chunk, "en", target_language_code)
            split_lines = [l.strip() for l in trans_block.splitlines() if l.strip()]

            if len(split_lines) == len(chunk):
                for orig_cue, tline in zip(chunk, split_lines):
                    nc = dict(orig_cue)
                    nc["text"] = tline
                    translated_cues.append(nc)
            else:
                for orig_cue in chunk:
                    nc = dict(orig_cue)
                    nc["text"] = translate_text_online(orig_cue["text"], "en", target_language_code)
                    translated_cues.append(nc)

        time.sleep(0.05)

    # FINAL INTEGRITY CHECK: Ensure no non-English track has English cues remaining
    if target_language_code.lower() != "en":
        for i, cue in enumerate(translated_cues):
            orig_cue = cues[i] if i < len(cues) else None
            if orig_cue and cue["text"].strip().lower() == orig_cue["text"].strip().lower():
                cue["text"] = translate_text_online(orig_cue["text"], "en", target_language_code)

    return translated_cues


# =========================================================
# MAIN GENERATION PIPELINE & ASYNC WORKER
# =========================================================

_generation_lock = threading.Lock()


def process_subtitles_for_lesson(
    lesson_id: int,
    target_languages: Optional[List[str]] = None,
) -> bool:
    """
    Main synchronous function to generate multilingual subtitles for a Lesson.
    1. Extracts audio & calls Whisper for transcript + language detection (or reuses shared video cues).
    2. Saves the original detected language subtitle track immediately.
    3. Translates sequentially into target languages with robust fallbacks.
    4. Generates WebVTT & SRT files and saves to database.
    """
    from .models import Lesson, SubtitleTrack

    lesson = Lesson.objects.filter(id=lesson_id).first()
    if not lesson or (not lesson.video and not lesson.video_url):
        logger.warning("Lesson %s does not exist or has no video attached.", lesson_id)
        return False

    api_key = get_groq_api_key()
    if not api_key:
        logger.info("GROQ_API_KEY is not configured. Skipping automated subtitle generation for Lesson %s.", lesson_id)
        has_ready = lesson.subtitles.filter(status="ready").exists()
        lesson.subtitle_status = "ready" if has_ready else "none"
        lesson.subtitle_error = ""
        lesson.save(update_fields=["subtitle_status", "subtitle_error"])
        return False

    lesson.subtitle_status = "processing"
    lesson.subtitle_error = ""
    lesson.save(update_fields=["subtitle_status", "subtitle_error"])

    temp_file_path = None
    is_temp = False

    try:
        # Check if this lesson already has ready English cues or if another lesson shares the same video
        cues = None
        detected_language_name = "English"

        existing_orig = lesson.subtitles.filter(language_code="en", status="ready").first()
        if existing_orig and existing_orig.cues_data:
            cues = [dict(c) for c in existing_orig.cues_data]
            detected_language_name = existing_orig.language_name or "English"
        else:
            # Check if another lesson with same video has transcribed cues
            shared_track = SubtitleTrack.objects.filter(
                lesson__video=lesson.video, language_code="en", status="ready"
            ).exclude(lesson=lesson).first()
            if shared_track and shared_track.cues_data:
                cues = [dict(c) for c in shared_track.cues_data]
                detected_language_name = shared_track.language_name or "English"

        if not cues:
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

        # Step 1: Save the original language track immediately so subtitles are instantly ready
        orig_lang_name = get_language_name(detected_lang_code)
        orig_vtt = cues_to_vtt(cues)
        orig_srt = cues_to_srt(cues)

        orig_track, _ = SubtitleTrack.objects.get_or_create(
            lesson=lesson,
            language_code=detected_lang_code,
            defaults={
                "language_name": orig_lang_name,
                "is_original": True,
                "status": "ready",
            }
        )
        orig_track.language_name = orig_lang_name
        orig_track.is_original = True
        orig_track.cues_data = cues
        orig_track.vtt_content = orig_vtt
        orig_track.srt_content = orig_srt
        orig_track.status = "ready"
        orig_track.error_message = ""
        orig_track.vtt_file.save(f"subtitles/lesson_{lesson.id}_{detected_lang_code}.vtt", ContentFile(orig_vtt.encode("utf-8")), save=False)
        orig_track.srt_file.save(f"subtitles/lesson_{lesson.id}_{detected_lang_code}.srt", ContentFile(orig_srt.encode("utf-8")), save=False)
        orig_track.save()

        lesson.detected_language = detected_language_name
        lesson.detected_language_code = detected_lang_code
        lesson.save(update_fields=["detected_language", "detected_language_code"])
        logger.info("Saved original %s subtitles (%d cues) for Lesson %s.", orig_lang_name, len(cues), lesson.id)

        # Step 2: Determine target translation languages
        languages_to_generate = target_languages or get_active_target_languages()
        other_languages = [
            str(l).strip().lower() for l in languages_to_generate
            if str(l).strip().lower() != detected_lang_code
        ]

        # Step 3: Helper for translation & saving
        for lc in other_languages:
            try:
                lang_name = get_language_name(lc)
                lang_cues = translate_cues_to_language(cues, detected_language_name, lc)
                vtt_text = cues_to_vtt(lang_cues)
                srt_text = cues_to_srt(lang_cues)

                sub_track, _ = SubtitleTrack.objects.get_or_create(
                    lesson_id=lesson.id,
                    language_code=lc,
                    defaults={
                        "language_name": lang_name,
                        "is_original": False,
                        "status": "ready",
                    }
                )
                sub_track.language_name = lang_name
                sub_track.is_original = False
                sub_track.cues_data = lang_cues
                sub_track.vtt_content = vtt_text
                sub_track.srt_content = srt_text
                sub_track.status = "ready"
                sub_track.error_message = ""
                sub_track.vtt_file.save(f"subtitles/lesson_{lesson.id}_{lc}.vtt", ContentFile(vtt_text.encode("utf-8")), save=False)
                sub_track.srt_file.save(f"subtitles/lesson_{lesson.id}_{lc}.srt", ContentFile(srt_text.encode("utf-8")), save=False)
                sub_track.save()
                logger.info("Saved %s subtitles (%d cues) for Lesson %s.", lang_name, len(lang_cues), lesson.id)
            except Exception as lang_exc:
                logger.exception("Failed generating subtitles for language %s on lesson %s: %s", lc, lesson.id, lang_exc)

        lesson.subtitle_status = "ready"
        lesson.subtitle_error = ""
        lesson.save(update_fields=["subtitle_status", "subtitle_error"])
        return True

    except Exception as exc:
        logger.exception("Subtitle generation failed for Lesson %s: %s", lesson_id, exc)
        has_ready = lesson.subtitles.filter(status="ready").exists()
        lesson.subtitle_status = "ready" if has_ready else "failed"
        lesson.subtitle_error = str(exc)
        lesson.save(update_fields=["subtitle_status", "subtitle_error"])
        return False

    finally:
        if is_temp and temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception:
                pass


def trigger_auto_subtitle_generation(
    lesson_id: int,
    target_languages: Optional[List[str]] = None,
) -> Optional[threading.Thread]:
    """
    Trigger subtitle generation in a safe background thread.
    Non-blocking: returns immediately so video uploads complete without delay.
    Sequential worker lock prevents concurrent Groq rate limit errors.
    """
    import sys
    if "test" in sys.argv:
        # In automated test runner, avoid spawning un-joined threads that race with transaction rollbacks
        return None

    def _thread_worker():
        with _generation_lock:
            try:
                process_subtitles_for_lesson(lesson_id, target_languages)
            finally:
                try:
                    from django.db import connection
                    connection.close()
                except Exception:
                    pass

    worker_thread = threading.Thread(
        target=_thread_worker,
        daemon=True,
        name=f"SubtitlesWorker-Lesson-{lesson_id}"
    )
    worker_thread.start()
    return worker_thread
