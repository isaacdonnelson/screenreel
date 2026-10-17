"""Word timings, anchor scheduling, and the capture-to-output retimer.

The voice is the clock. Presses are placed on the anchor word. Waits are
elastic afterwards so a slow page does not move the word.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from screenreel_make.lesson import Lesson, LessonError, words_in


def cache_key(text: str, voice_id: str, model: str, settings: dict[str, Any]) -> str:
    blob = json.dumps(
        {"model": model, "settings": settings, "text": text, "voice": voice_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def words_from_alignment(alignment: dict[str, Any]) -> list[dict[str, Any]]:
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[dict[str, Any]] = []
    current = ""
    wstart = 0.0
    wend = 0.0
    for char, start, end in zip(chars, starts, ends):
        if str(char).isspace():
            if current:
                words.append({"word": current, "start": float(wstart), "end": float(wend)})
                current = ""
            continue
        if not current:
            wstart = float(start)
        current += str(char)
        wend = float(end)
    if current:
        words.append({"word": current, "start": float(wstart), "end": float(wend)})
    return words


def estimate_words(text: str, seconds_per_word: float = 0.32) -> list[dict[str, Any]]:
    words = []
    cursor = 0.0
    for word in words_in(text):
        words.append({"word": word, "start": round(cursor, 3), "end": round(cursor + seconds_per_word, 3)})
        cursor += seconds_per_word
    return words


def find_word_start(words: list[dict[str, Any]], token: str) -> float | None:
    for word in words:
        if str(word["word"]).strip(".,!?;:") == token:
            return float(word["start"])
    return None


def schedule(lesson: Lesson, tracks: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Place every anchored press on its word, in output time."""
    lead = lesson.lead
    cursor = 0.25
    actions: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if lesson.intro:
        intro_words = tracks.get("intro") or estimate_words(lesson.intro)
        intro_end = float(intro_words[-1]["end"]) if intro_words else 0.0
        steps.append({"id": "intro", "label": "Intro", "t0": cursor, "t1": cursor + intro_end + 0.4, "actions": []})
        cursor += intro_end + 0.4
    for step in lesson.steps:
        step_t0 = cursor
        words = tracks.get(step.id) or estimate_words(step.say)
        shifted = [
            {"word": word["word"], "start": float(word["start"]) + cursor + 0.25, "end": float(word["end"]) + cursor + 0.25}
            for word in words
        ]
        counts: dict[str, int] = {}
        step_actions = []
        for action in step.actions:
            if action.kind in {"goto", "scroll_into_view", "wait_for", "script"} and not action.at:
                continue
            if action.kind == "hold" and not action.at:
                continue
            counts[action.kind] = counts.get(action.kind, 0) + 1
            ident = f"{step.id}.{action.kind}-{counts[action.kind]}"
            anchor_t = None
            if action.at:
                anchor_t = find_word_start(shifted, action.at)
                if anchor_t is None:
                    raise LessonError(
                        f"Anchor \"{action.at}\" is not a word in this step's narration",
                        step=step.id,
                        code="anchor_missing",
                    )
            press = (anchor_t + lead) if anchor_t is not None else cursor + 0.5
            planned = {
                "id": ident,
                "step": step.id,
                "kind": action.kind,
                "at": action.at,
                "anchor_t": anchor_t,
                "press": round(press, 3),
                "lead": lead,
                "text": action.text,
                "spec": action.spec,
                "result": action.result,
                "wait_for": action.wait_for,
                "expect_value": action.expect_value,
                "paste": action.paste,
                "hold": action.seconds,
            }
            actions.append(planned)
            step_actions.append({
                "id": ident,
                "press": planned["press"],
                "anchor": action.at,
                "anchor_t": anchor_t,
                "slip": 0.0,
            })
        spoken_end = shifted[-1]["end"] if shifted else step_t0 + 0.8
        last_press = max((item["press"] for item in step_actions), default=spoken_end)
        step_t1 = max(spoken_end, last_press) + 0.4
        steps.append({
            "id": step.id,
            "label": step.label,
            "t0": round(step_t0, 3),
            "t1": round(step_t1, 3),
            "actions": step_actions,
        })
        cursor = step_t1
    title = float((lesson.style.get("title_card") or {}).get("seconds") or 0)
    end_card = float((lesson.style.get("end_card") or {}).get("seconds") or 0)
    return {
        "duration": round(cursor + end_card, 3),
        "title_card": title,
        "end_card": end_card,
        "actions": actions,
        "steps": steps,
        "warnings": warnings,
        "lead": lead,
    }


def retime(schedule_doc: dict[str, Any], actuals: list[dict[str, Any]], capture_duration: float) -> dict[str, Any]:
    """Fit capture time onto the voice clock.

    Idle gaps are cut so each press lands on its anchor. A press that is still
    more than 0.4 s late after that cut fails. Up to 0.4 s is a sync_slip warning.
    """
    by_id = {item["id"]: item for item in actuals}
    warnings: list[dict[str, Any]] = []
    pieces: list[dict[str, Any]] = []
    source_cursor = 0.0
    output_cursor = 0.0
    placed: list[dict[str, Any]] = []
    failed = False
    for planned in schedule_doc.get("actions") or []:
        actual = by_id.get(planned["id"])
        if actual is None or planned.get("anchor_t") is None:
            continue
        capture_t = float(actual["capture"])
        target = float(planned["press"])
        if capture_t < source_cursor:
            capture_t = source_cursor
        gap_source = capture_t - source_cursor
        gap_output = max(0.0, target - output_cursor)
        # Keep a short real-time window around the press itself.
        if gap_source > gap_output + 0.02:
            keep = gap_output
            cut_end = capture_t - keep
            if cut_end > source_cursor + 0.02:
                pieces.append({
                    "source_start": round(source_cursor, 3),
                    "source_end": round(cut_end, 3),
                    "output_start": round(output_cursor, 3),
                    "speed": None,
                    "cut": True,
                })
            source_cursor = capture_t - keep
            if keep > 0.02:
                speed = 1.0
                pieces.append({
                    "source_start": round(source_cursor, 3),
                    "source_end": round(capture_t, 3),
                    "output_start": round(output_cursor, 3),
                    "speed": speed,
                    "cut": False,
                })
                output_cursor += keep / speed
                source_cursor = capture_t
        else:
            speed = 1.0 if gap_source <= 0.02 else gap_source / max(gap_output, 0.05)
            speed = min(8.0, max(1.0, speed))
            if gap_source > 0.02:
                pieces.append({
                    "source_start": round(source_cursor, 3),
                    "source_end": round(capture_t, 3),
                    "output_start": round(output_cursor, 3),
                    "speed": round(speed, 3),
                    "cut": False,
                })
                output_cursor += gap_source / speed
                source_cursor = capture_t
        slip = output_cursor - target
        if slip > 0.4:
            failed = True
            warnings.append({
                "code": "sync_slip",
                "step": planned["step"],
                "t": round(target, 3),
                "message": f"{planned['id']} is {slip:.2f} s late and the wait before it cannot absorb that",
                "fix": "Move the anchor word later in the line, or shorten the page wait",
            })
        elif slip > 0.15:
            warnings.append({
                "code": "sync_slip",
                "step": planned["step"],
                "t": round(target, 3),
                "message": f"{planned['id']} lands {slip:.2f} s after its word",
                "fix": None,
            })
        placed.append({"id": planned["id"], "capture": capture_t, "output": round(output_cursor, 3), "slip": round(slip, 3)})
    tail = max(0.0, float(capture_duration) - source_cursor)
    if tail > 0.02:
        pieces.append({
            "source_start": round(source_cursor, 3),
            "source_end": round(float(capture_duration), 3),
            "output_start": round(output_cursor, 3),
            "speed": 1.0,
            "cut": False,
        })
        output_cursor += tail
    return {
        "pieces": pieces,
        "duration": round(output_cursor, 3),
        "presses": placed,
        "warnings": warnings,
        "ok": not failed,
    }


def source_time(retime_doc: dict[str, Any], output_t: float) -> float:
    for piece in retime_doc.get("pieces") or []:
        if piece.get("cut"):
            continue
        speed = float(piece.get("speed") or 1)
        length = (float(piece["source_end"]) - float(piece["source_start"])) / max(speed, 0.25)
        if output_t <= float(piece["output_start"]) + length + 1e-4:
            into = max(0.0, output_t - float(piece["output_start"]))
            return float(piece["source_start"]) + into * speed
    pieces = retime_doc.get("pieces") or []
    if not pieces:
        return output_t
    return float(pieces[-1]["source_end"])


def output_time(retime_doc: dict[str, Any], source_t: float) -> float:
    for piece in retime_doc.get("pieces") or []:
        if piece.get("cut"):
            if float(piece["source_start"]) <= source_t <= float(piece["source_end"]):
                return float(piece["output_start"])
            continue
        if source_t <= float(piece["source_end"]) + 1e-4:
            if source_t < float(piece["source_start"]):
                return float(piece["output_start"])
            speed = float(piece.get("speed") or 1)
            into = max(0.0, source_t - float(piece["source_start"]))
            return float(piece["output_start"]) + into / max(speed, 0.25)
    return float(retime_doc.get("duration") or 0)


def load_cached_voice(cache_dir: str, key: str) -> dict[str, Any] | None:
    path = os.path.join(cache_dir, key + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def store_cached_voice(cache_dir: str, key: str, payload: dict[str, Any]) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, key + ".json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def fetch_elevenlabs(
    text: str,
    voice_id: str,
    model: str,
    api_key: str,
    opener: Callable[..., Any] | None = None,
    attempts: int = 3,
) -> dict[str, Any]:
    """POST with-timestamps, then text-to-dialogue. Retry three times."""
    open_url = opener or urllib.request.urlopen
    endpoints = [
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
        "https://api.elevenlabs.io/v1/text-to-dialogue/with-timestamps",
    ]
    body_for = [
        {"text": text, "model_id": model},
        {"inputs": [{"text": text, "voice_id": voice_id}], "model_id": model},
    ]
    last_error = "voice request failed"
    for attempt in range(attempts):
        for url, body in zip(endpoints, body_for):
            request = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"xi-api-key": api_key, "content-type": "application/json", "accept": "application/json"},
                method="POST",
            )
            try:
                with open_url(request, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                last_error = f"ElevenLabs HTTP {error.code}"
                if error.code not in {400, 404, 422}:
                    break
                continue
            except Exception as error:  # noqa: BLE001 - surfaced as voice_unavailable
                last_error = str(error)
                break
            alignment = payload.get("alignment") or payload.get("normalized_alignment") or {}
            audio = payload.get("audio_base64") or ""
            return {
                "words": words_from_alignment(alignment),
                "audio_base64": audio,
            }
        time.sleep(min(2.0, 0.2 * (2 ** attempt)))
    raise LessonError(last_error, code="voice_unavailable")


def prepare_tracks(
    lesson: Lesson,
    cache_dir: str,
    allow_estimated: bool = False,
    reuse: bool = True,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    provider = str(lesson.voice.get("provider") or "elevenlabs")
    voice_id = str(lesson.voice.get("voice_id") or "")
    model = str(lesson.voice.get("model") or "eleven_v3")
    settings = {key: lesson.voice.get(key) for key in ("stability", "similarity_boost", "style") if key in lesson.voice}
    texts: list[tuple[str, str]] = []
    if lesson.intro:
        texts.append(("intro", lesson.intro))
    for step in lesson.steps:
        texts.append((step.id, step.say))
    tracks: dict[str, list[dict[str, Any]]] = {}
    audio: dict[str, str] = {}
    estimated = provider == "estimated" or allow_estimated
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if provider == "elevenlabs" and not allow_estimated:
        if not api_key:
            raise LessonError(
                "ELEVENLABS_API_KEY is not set. Pass --allow-estimated-voice to plan with even word timings.",
                code="voice_unavailable",
            )
        estimated = False
    for ident, text in texts:
        key = cache_key(text, voice_id, model, settings)
        cached = load_cached_voice(cache_dir, key) if reuse else None
        if cached and cached.get("words"):
            tracks[ident] = cached["words"]
            if cached.get("audio_base64"):
                audio[ident] = cached["audio_base64"]
            continue
        if estimated:
            tracks[ident] = estimate_words(text)
            continue
        fetched = fetch_elevenlabs(text, voice_id, model, api_key, opener=opener)
        store_cached_voice(cache_dir, key, {"words": fetched["words"], "audio_base64": fetched.get("audio_base64") or ""})
        tracks[ident] = fetched["words"]
        if fetched.get("audio_base64"):
            audio[ident] = fetched["audio_base64"]
    return {"tracks": tracks, "audio": audio, "estimated": estimated and provider != "elevenlabs"}


def write_audio_files(audio: dict[str, str], directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    for ident, encoded in audio.items():
        if not encoded:
            continue
        path = os.path.join(directory, f"{ident}.mp3")
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(encoded))
