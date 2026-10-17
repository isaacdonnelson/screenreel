"""Voice, ducked music, and click SFX, then two-pass loudnorm.

Music drops about 14 dB under the voice. The envelope attack is 150 ms and
the release is 400 ms, which is the sidechain the mix asks for.
"""

from __future__ import annotations

import json
import os
import subprocess
import wave
from typing import Any

import numpy as np

SR = 44100


def write_wav(path: str, audio: np.ndarray, sr: int = SR) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pcm = np.clip(audio, -1.0, 1.0)
    with wave.open(path, "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes((pcm * 32767.0).astype(np.int16).tobytes())


def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "r") as handle:
        sr = handle.getframerate()
        count = handle.getnframes()
        raw = handle.readframes(count)
        channels = handle.getnchannels()
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, sr


def click_sample(sr: int = SR) -> np.ndarray:
    n = int(0.045 * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(7)
    noise = rng.uniform(-1.0, 1.0, n).astype(np.float32)
    return noise * np.exp(-t * 90).astype(np.float32)


def key_sample(sr: int = SR) -> np.ndarray:
    n = int(0.02 * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(11)
    tone = np.sin(2 * np.pi * 1800 * t) * np.exp(-t * 160)
    noise = rng.uniform(-0.3, 0.3, n)
    return (0.6 * tone + 0.4 * noise).astype(np.float32)


def tone_for_words(words: list[dict[str, Any]], sr: int = SR) -> np.ndarray:
    if not words:
        return np.zeros(int(0.4 * sr), np.float32)
    end = float(words[-1]["end"]) + 0.05
    audio = np.zeros(max(1, int(end * sr)), np.float32)
    rng = np.random.default_rng(5)
    for word in words:
        a = int(float(word["start"]) * sr)
        b = max(a + 1, int(float(word["end"]) * sr))
        b = min(b, audio.size)
        length = b - a
        window = np.hanning(length).astype(np.float32)
        audio[a:b] += rng.uniform(-0.35, 0.35, length).astype(np.float32) * window
    return audio


def place(times: list[float], clip: np.ndarray, duration: float, gain: float, sr: int = SR) -> np.ndarray:
    total = max(1, int(duration * sr))
    out = np.zeros(total, np.float32)
    scaled = clip * gain
    for t in times:
        start = int(max(0.0, t) * sr)
        stop = min(total, start + scaled.size)
        if stop > start:
            out[start:stop] += scaled[: stop - start]
    return out


def loop_bed(duration: float, sr: int = SR) -> np.ndarray:
    """Quiet two-note pad. Used when the lesson does not name a music file."""
    n = max(1, int(duration * sr))
    t = np.arange(n) / sr
    bed = 0.08 * (np.sin(2 * np.pi * 196 * t) + 0.6 * np.sin(2 * np.pi * 247 * t))
    fade_in = min(n, int(sr))
    fade_out = min(n, int(2 * sr))
    bed[:fade_in] *= np.linspace(0, 1, fade_in)
    bed[-fade_out:] *= np.linspace(1, 0, fade_out)
    return bed.astype(np.float32)


def load_audio(path: str, duration: float, sr: int = SR) -> np.ndarray:
    wav = path if path.endswith(".wav") else path + ".decoded.wav"
    if not path.endswith(".wav"):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(sr), wav],
            check=True,
        )
    audio, file_sr = read_wav(wav if not path.endswith(".wav") else path)
    if file_sr != sr:
        raise RuntimeError(f"unexpected sample rate {file_sr}")
    target = int(duration * sr)
    if audio.size < target:
        repeats = int(np.ceil(target / max(audio.size, 1)))
        audio = np.tile(audio, repeats)
    return audio[:target].astype(np.float32)


def sidechain(music: np.ndarray, voice: np.ndarray, sr: int = SR, depth_db: float = 14.0, attack: float = 0.15, release: float = 0.40) -> np.ndarray:
    """Drop the music by depth_db while the voice is present."""
    if music.size == 0:
        return music
    voice = voice[: music.size] if voice.size >= music.size else np.pad(voice, (0, music.size - voice.size))
    mag = np.abs(voice)
    # Follow the envelope at 200 Hz. A per-sample Python loop is too slow at 44.1 kHz.
    hop = max(1, sr // 200)
    coarse = mag[::hop]
    env_c = np.zeros_like(coarse)
    attack_c = np.exp(-1.0 / max(attack * (sr / hop), 1.0))
    release_c = np.exp(-1.0 / max(release * (sr / hop), 1.0))
    level = 0.0
    for index, sample in enumerate(coarse):
        coeff = attack_c if sample > level else release_c
        level = coeff * level + (1.0 - coeff) * float(sample)
        env_c[index] = level
    env = np.interp(np.arange(mag.size), np.arange(env_c.size) * hop, env_c).astype(np.float32)
    # Speech above about -40 dBFS opens the full duck. A percentile gate
    # stayed half-open when the voice only covered part of the file.
    threshold = 10 ** (-30.0 / 20.0)
    gate = np.clip(env / threshold, 0.0, 1.0)
    dipped = 10 ** (-abs(depth_db) / 20.0)
    gain = 1.0 - gate * (1.0 - dipped)
    return (music * gain).astype(np.float32)


def mix(
    voice_tracks: list[tuple[str, float]],
    press_times: list[float],
    key_times: list[float],
    duration: float,
    out_wav: str,
    music_path: str = "",
    duck_db: float = 14.0,
) -> dict[str, Any]:
    total = max(1, int(duration * SR))
    voice = np.zeros(total, np.float32)
    for path, start in voice_tracks:
        clip, _sr = _decode(path)
        # 20 ms fades.
        fade = min(int(0.02 * SR), max(1, clip.size // 4))
        clip = clip.copy()
        clip[:fade] *= np.linspace(0, 1, fade)
        clip[-fade:] *= np.linspace(1, 0, fade)
        begin = int(max(0.0, start) * SR)
        stop = min(total, begin + clip.size)
        if stop > begin:
            voice[begin:stop] += clip[: stop - begin]
    if music_path and os.path.exists(music_path):
        music = load_audio(music_path, duration)
    else:
        music = loop_bed(duration)
    ducked = sidechain(music, voice, depth_db=abs(duck_db))
    clicks = place(press_times, click_sample(), duration, gain=10 ** (-24 / 20))
    keys = place(key_times, key_sample(), duration, gain=10 ** (-32 / 20))
    mixed = voice + ducked + clicks + keys
    write_wav(out_wav, mixed)
    return {"voice_peak": float(np.max(np.abs(voice)) if voice.size else 0), "path": out_wav}


def _decode(path: str) -> tuple[np.ndarray, int]:
    if path.endswith(".wav"):
        return read_wav(path)
    temporary = path + ".pcm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SR), temporary],
        check=True,
    )
    return read_wav(temporary)


def loudnorm(wav_path: str, video_path: str, output_path: str, lufs: float = -14.0, true_peak: float = -1.0) -> dict[str, Any]:
    measured = _analyze(wav_path, lufs, true_peak)
    normalized = wav_path + ".norm.wav"
    # linear=true disables the limiter, so the true peak walks past -1 dBTP.
    af = (
        f"loudnorm=I={lufs}:TP={true_peak}:LRA=11:measured_I={measured['input_i']}:"
        f"measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}:"
        f"measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path, "-af", af, normalized],
        check=True,
    )
    mastered = _approach_target(normalized, lufs, true_peak)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    _mux(mastered, video_path, output_path)
    report = measure_loudness(output_path)
    if abs(report["lufs"] - lufs) > 0.45 or report["true_peak"] > true_peak + 0.05:
        gain = lufs - report["lufs"]
        if report["true_peak"] > true_peak:
            gain = min(gain, true_peak - report["true_peak"] - 0.4)
        corrected = wav_path + ".trim.wav"
        _apply_gain(mastered, corrected, gain, true_peak)
        _mux(corrected, video_path, output_path)
        report = measure_loudness(output_path)
    report["path"] = output_path
    return report


def _approach_target(path: str, lufs: float, true_peak: float) -> str:
    report = measure_loudness(path)
    gain = lufs - report["lufs"]
    if abs(gain) <= 0.25 and report["true_peak"] <= true_peak:
        return path
    dest = path + ".level.wav"
    _apply_gain(path, dest, gain, true_peak)
    return dest


def _apply_gain(src: str, dest: str, gain_db: float, true_peak: float) -> None:
    # 0.3 dB under the ceiling so AAC does not push the peak back over.
    limit = 10 ** ((true_peak - 0.3) / 20.0)
    af = f"volume={gain_db:.3f}dB,alimiter=limit={limit:.5f}:level=false"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", af, dest],
        check=True,
    )


def _mux(wav_path: str, video_path: str, output_path: str) -> None:
    if video_path and os.path.exists(video_path):
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", video_path, "-i", wav_path,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-movflags", "+faststart", "-shortest",
                output_path,
            ],
            check=True,
        )
        return
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", output_path],
        check=True,
    )


def _analyze(path: str, lufs: float, true_peak: float) -> dict[str, str]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af", f"loudnorm=I={lufs}:TP={true_peak}:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    text = result.stderr
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(text[-400:])
    payload = json.loads(text[start: end + 1])
    return {
        "input_i": str(payload["input_i"]),
        "input_tp": str(payload["input_tp"]),
        "input_lra": str(payload["input_lra"]),
        "input_thresh": str(payload["input_thresh"]),
        "target_offset": str(payload["target_offset"]),
    }


def measure_loudness(path: str) -> dict[str, float]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    text = result.stderr
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else ""
    integrated = None
    true_peak = None
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith("I:") and "LUFS" in stripped:
            integrated = float(stripped.split("I:")[1].split("LUFS")[0].strip())
        if stripped.startswith("Peak:") and "dBFS" in stripped:
            true_peak = float(stripped.split("Peak:")[1].split("dBFS")[0].strip())
    if integrated is None or true_peak is None:
        raise RuntimeError(f"ebur128 did not report loudness for {path}")
    return {"lufs": integrated, "true_peak": true_peak}
