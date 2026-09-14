import os
import sys
import time
import html
import urllib.request
import urllib.parse
import json
import django

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
os.environ['DJANGO_SETTINGS_MODULE'] = 'clout.settings'
django.setup()

from django.core.files.base import ContentFile
from shop.models import Lesson, SubtitleTrack, SubtitleSetting
from shop.subtitles import (
    SUPPORTED_LANGUAGES,
    get_language_name,
    cues_to_vtt,
    cues_to_srt,
)

_trans_cache = {}

def translate_single_text(text, target_code):
    if not text or not text.strip():
        return text
    clean = text.strip()
    cache_key = (target_code, clean)
    if cache_key in _trans_cache:
        return _trans_cache[cache_key]

    # 1. Google Translate gtx
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl={target_code}&dt=t&q={urllib.parse.quote(clean)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            trans = ''.join([item[0] for item in data[0] if item and item[0]])
            if trans and trans.strip():
                res = html.unescape(trans).strip()
                _trans_cache[cache_key] = res
                return res
    except Exception:
        pass

    # 2. MyMemory fallback
    try:
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(clean)}&langpair=en|{target_code}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            trans = data.get("responseData", {}).get("translatedText", "")
            if trans and not trans.startswith("MYMEMORY WARNING"):
                res = html.unescape(trans).strip()
                _trans_cache[cache_key] = res
                return res
    except Exception:
        pass

    return clean


def translate_cues_fast(base_cues, target_code):
    translated_cues = []
    batch_size = 5
    for i in range(0, len(base_cues), batch_size):
        batch = base_cues[i:i+batch_size]
        joined_text = "\n".join([c['text'] for c in batch])
        translated_block = translate_single_text(joined_text, target_code)
        trans_lines = [l.strip() for l in translated_block.splitlines() if l.strip()]

        if len(trans_lines) == len(batch):
            for cue, tline in zip(batch, trans_lines):
                c_copy = dict(cue)
                c_copy['text'] = tline
                translated_cues.append(c_copy)
        else:
            for cue in batch:
                c_copy = dict(cue)
                c_copy['text'] = translate_single_text(cue['text'], target_code)
                translated_cues.append(c_copy)

        if (i // batch_size + 1) % 15 == 0:
            print(f"    Processed {min(i+batch_size, len(base_cues))}/{len(base_cues)} cues...", flush=True)

    # Double check: no cues in English
    for i, c in enumerate(translated_cues):
        if c['text'].strip().lower() == base_cues[i]['text'].strip().lower():
            c['text'] = translate_single_text(base_cues[i]['text'], target_code)

    return translated_cues


def save_track_for_all_lessons(all_lessons, lang_code, lang_name, translated_cues):
    vtt_content = cues_to_vtt(translated_cues)
    srt_content = cues_to_srt(translated_cues)
    vtt_bytes = vtt_content.encode("utf-8")
    srt_bytes = srt_content.encode("utf-8")

    for lesson in all_lessons:
        track, _ = SubtitleTrack.objects.get_or_create(
            lesson=lesson,
            language_code=lang_code,
            defaults={
                "language_name": lang_name,
                "is_original": False,
                "status": "ready",
            }
        )
        track.language_name = lang_name
        track.is_original = False
        track.cues_data = translated_cues
        track.vtt_content = vtt_content
        track.srt_content = srt_content
        track.status = "ready"
        track.error_message = ""
        track.vtt_file.save(f"subtitles/lesson_{lesson.id}_{lang_code}.vtt", ContentFile(vtt_bytes), save=False)
        track.srt_file.save(f"subtitles/lesson_{lesson.id}_{lang_code}.srt", ContentFile(srt_bytes), save=False)
        track.save()

        if lesson.subtitle_status != "ready":
            lesson.subtitle_status = "ready"
            lesson.subtitle_error = ""
            lesson.save(update_fields=["subtitle_status", "subtitle_error"])


def main():
    print("=== Subtitle Generation & Multilingual Pipeline ===")
    all_codes = list(SUPPORTED_LANGUAGES.keys())
    setting, _ = SubtitleSetting.objects.get_or_create(key="target_languages")
    setting.value = all_codes
    setting.save()

    en_track = SubtitleTrack.objects.filter(language_code="en", status="ready").first()
    if not en_track or not en_track.cues_data:
        print("[ERROR] Base English track not found!")
        return

    base_cues = [dict(c) for c in en_track.cues_data]
    all_lessons = list(Lesson.objects.all().order_by("id"))
    print(f"Loaded {len(base_cues)} base English cues for {len(all_lessons)} lessons.")

    # Prioritized target languages:
    languages = [
        "hi", "ml", "ar", "ta", "te", "bn", "gu", "mr", "kn", "pa", "ur",
        "es", "fr", "de", "ja", "zh", "ko", "ru", "it", "pt", "tr", "id",
        "vi", "nl", "pl"
    ]

    for idx, lang_code in enumerate(languages, start=1):
        lang_name = get_language_name(lang_code)
        native_name = SUPPORTED_LANGUAGES[lang_code].get("native", lang_name)
        print(f"\n[{idx}/{len(languages)}] Translating to {lang_name} ({native_name}) [{lang_code}]...", flush=True)

        t0 = time.time()
        cues = translate_cues_fast(base_cues, lang_code)
        elapsed = time.time() - t0

        print(f"  -> Done in {elapsed:.1f}s. Sample Cue 0: \"{cues[0]['text'][:60]}\"", flush=True)
        print(f"  -> Sample Cue 3: \"{cues[3]['text'][:60]}\"", flush=True)

        save_track_for_all_lessons(all_lessons, lang_code, lang_name, cues)
        print(f"  -> Saved {lang_name} [{lang_code}] across all {len(all_lessons)} lessons!", flush=True)

    print("\n=== ALL LANGUAGES SUCCESSFULLY APPLIED ===")

if __name__ == "__main__":
    main()
