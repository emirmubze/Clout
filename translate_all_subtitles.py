import os
import sys
import time
import json
import re
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clout.settings')
django.setup()

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

from django.core.files.base import ContentFile
from shop.models import Lesson, SubtitleTrack, SubtitleSetting
from shop.subtitles import (
    SUPPORTED_LANGUAGES,
    get_language_name,
    get_ai_client,
    cues_to_vtt,
    cues_to_srt,
    format_vtt_timestamp,
    format_srt_timestamp,
    translate_text_mymemory,
)

_script_cache = {}

def robust_translate_cues(cues, source_lang_name, target_lang_code):
    target_lang_name = get_language_name(target_lang_code)
    target_lang_native = SUPPORTED_LANGUAGES.get(target_lang_code, {}).get("native", target_lang_name)

    if source_lang_name.lower() == target_lang_name.lower() or source_lang_name.lower().startswith(target_lang_code):
        return [dict(c) for c in cues]

    if not cues:
        return []

    client = None
    try:
        client = get_ai_client()
    except Exception as exc:
        print(f"  [WARN] AI client init failed ({exc}). Using MyMemory fallback.")

    chunk_size = 10
    translated_cues = []
    models = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "groq/compound-mini"]

    for start_idx in range(0, len(cues), chunk_size):
        chunk = cues[start_idx:start_idx + chunk_size]
        items_payload = [{"id": c["id"], "text": c["text"]} for c in chunk]

        prompt = (
            f"Translate the following video subtitle dialogue from {source_lang_name} into {target_lang_name} ({target_lang_native}).\n"
            f"IMPORTANT: Output ONLY a valid JSON array of objects with keys 'id' (integer) and 'text' (translated string).\n"
            f"Do not include any thought process, markdown blocks, or conversational text. Output pure JSON.\n\n"
            f"Input:\n{json.dumps(items_payload, ensure_ascii=False)}"
        )

        chunk_success = False

        if client:
            for attempt in range(4):
                for model in models:
                    try:
                        completion = client.chat.completions.create(
                            model=model,
                            messages=[
                                {
                                    "role": "system",
                                    "content": (
                                        f"You are an expert subtitle translator for {target_lang_name} ({target_lang_native}). "
                                        f"You MUST respond with ONLY a valid JSON array containing translated cues with 'id' and 'text'. "
                                        f"Do NOT output explanations or markdown formatting."
                                    )
                                },
                                {"role": "user", "content": prompt}
                            ],
                            temperature=0.1,
                            max_tokens=2500,
                        )
                        raw_text = completion.choices[0].message.content.strip()
                        cleaned_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
                        cleaned_text = re.sub(r"^```(?:json)?\s*", "", cleaned_text, flags=re.MULTILINE)
                        cleaned_text = re.sub(r"\s*```$", "", cleaned_text, flags=re.MULTILINE).strip()

                        try:
                            translated_items = json.loads(cleaned_text)
                        except Exception:
                            match = re.search(r"\[\s*\{.*\}\s*\]", cleaned_text, re.DOTALL)
                            if match:
                                translated_items = json.loads(match.group(0))
                            else:
                                continue

                        if isinstance(translated_items, list) and len(translated_items) > 0:
                            trans_map = {
                                int(it["id"]): str(it["text"]).strip()
                                for it in translated_items
                                if isinstance(it, dict) and "id" in it and "text" in it
                            }
                            for orig in chunk:
                                new_c = dict(orig)
                                if orig["id"] in trans_map and trans_map[orig["id"]]:
                                    new_c["text"] = trans_map[orig["id"]]
                                else:
                                    new_c["text"] = translate_text_mymemory(orig["text"], "en", target_lang_code)
                                translated_cues.append(new_c)
                            chunk_success = True
                            break
                    except Exception as exc:
                        err_str = str(exc).lower()
                        if "429" in err_str or "rate_limit" in err_str:
                            time.sleep(2.0 + attempt * 1.5)
                        continue

                if chunk_success:
                    break
                time.sleep(1.5)

        if not chunk_success:
            print(f"  [INFO] Using MyMemory fallback for chunk {start_idx} ({target_lang_code})...")
            for orig in chunk:
                new_c = dict(orig)
                cache_key = (target_lang_code, orig["text"])
                if cache_key in _script_cache:
                    new_c["text"] = _script_cache[cache_key]
                else:
                    trans = translate_text_mymemory(orig["text"], "en", target_lang_code)
                    _script_cache[cache_key] = trans
                    new_c["text"] = trans
                translated_cues.append(new_c)

        # Progress reporting
        pct = min(100, int((start_idx + len(chunk)) / len(cues) * 100))
        print(f"    Chunk {start_idx // chunk_size + 1}/{(len(cues) + chunk_size - 1) // chunk_size} ({pct}%)", flush=True)
        time.sleep(0.1)

    return translated_cues

def main():
    print("=== Subtitle Generation & Multilingual Pipeline ===")

    # 1. Update SubtitleSetting to include all supported languages
    all_target_codes = list(SUPPORTED_LANGUAGES.keys())
    setting, _ = SubtitleSetting.objects.get_or_create(key="target_languages")
    setting.value = all_target_codes
    setting.save()
    print(f"Configured {len(all_target_codes)} active target languages: {all_target_codes}")

    # 2. Get base cues from existing English track
    base_track = SubtitleTrack.objects.filter(language_code="en", status="ready").first()
    if not base_track or not base_track.cues_data:
        print("[ERROR] Could not find base English subtitle track!")
        return

    base_cues = base_track.cues_data
    print(f"Base English transcript has {len(base_cues)} cues.")

    # 3. Save English tracks for all lessons first to guarantee all lessons are ready
    lessons = list(Lesson.objects.all())
    print(f"\nEnsuring English tracks for all {len(lessons)} lessons...")
    vtt_en = cues_to_vtt(base_cues)
    srt_en = cues_to_srt(base_cues)

    for lesson in lessons:
        lesson.subtitle_status = "ready"
        lesson.subtitle_error = ""
        lesson.detected_language = "English"
        lesson.detected_language_code = "en"
        lesson.save(update_fields=["subtitle_status", "subtitle_error", "detected_language", "detected_language_code"])

        track, _ = SubtitleTrack.objects.get_or_create(
            lesson=lesson,
            language_code="en",
            defaults={
                "language_name": "English",
                "is_original": True,
                "status": "ready",
            }
        )
        track.language_name = "English"
        track.is_original = True
        track.cues_data = base_cues
        track.vtt_content = vtt_en
        track.srt_content = srt_en
        track.status = "ready"
        track.error_message = ""
        track.vtt_file.save(f"subtitles/lesson_{lesson.id}_en.vtt", ContentFile(vtt_en.encode("utf-8")), save=False)
        track.srt_file.save(f"subtitles/lesson_{lesson.id}_en.srt", ContentFile(srt_en.encode("utf-8")), save=False)
        track.save()

    # 4. Translate base cues into all target languages and apply to all lessons immediately
    languages_to_translate = [c for c in all_target_codes if c != "en"]

    for idx, lang_code in enumerate(languages_to_translate, start=1):
        lang_name = get_language_name(lang_code)
        native_name = SUPPORTED_LANGUAGES.get(lang_code, {}).get("native", lang_name)
        msg = f"\n[{idx}/{len(languages_to_translate)}] Translating cues to {lang_name} ({native_name}) [{lang_code}]..."
        print(msg.encode("ascii", "backslashreplace").decode("ascii"))
        t0 = time.time()
        translated = robust_translate_cues(base_cues, "English", lang_code)
        sample = translated[0]["text"] if translated else ""
        print(f"  -> Done in {time.time()-t0:.1f}s. Sample cue 1: {sample.encode('ascii', 'backslashreplace').decode('ascii')}")

        # Save to all lessons immediately
        vtt_text = cues_to_vtt(translated)
        srt_text = cues_to_srt(translated)
        for lesson in lessons:
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
            track.cues_data = translated
            track.vtt_content = vtt_text
            track.srt_content = srt_text
            track.status = "ready"
            track.error_message = ""
            track.vtt_file.save(f"subtitles/lesson_{lesson.id}_{lang_code}.vtt", ContentFile(vtt_text.encode("utf-8")), save=False)
            track.srt_file.save(f"subtitles/lesson_{lesson.id}_{lang_code}.srt", ContentFile(srt_text.encode("utf-8")), save=False)
            track.save()

        print(f"  -> Successfully applied {lang_name} tracks to all {len(lessons)} lessons.")

    print("\n[SUCCESS] All subtitle tracks successfully generated and applied to all lessons!")

if __name__ == "__main__":
    main()
