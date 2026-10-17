"""Phase 2: scheduler, mix, captions, cursor fade, and render budget."""

import base64
import json
import os
from pathlib import Path

import numpy as np
import pytest

from screenreel_make.audio import click_sample, key_sample, loop_bed, loudnorm, sidechain, write_wav
from screenreel_make.camera import Shot, build_camera_track
from screenreel_make.captions import validate_cues, wrap_cues, write_srt, write_vtt
from screenreel_make.command import _check_frozen, _check_loudness, _check_sync
from screenreel_make.cursor import Point, cursor_opacity, plan_travel
from screenreel_make.lesson import load_lesson
from screenreel_make.render import render_movie
from screenreel_make.voice import align_clip, estimate_words, fetch_elevenlabs, prepare_tracks, schedule

ROOT = Path(__file__).resolve().parents[1]
SR = 44100


def _lesson(tmp_path: Path, body: str):
    path = tmp_path / "lesson.yaml"
    path.write_text(body, encoding="utf-8")
    return load_lesson(str(path))


def test_anchored_press_lands_on_the_word_and_loose_actions_are_even(tmp_path):
    lesson = _lesson(
        tmp_path,
        """
lesson: sched
title: Schedule
app: {base_url: https://example.test}
voice: {lead: -0.08}
steps:
  - id: one
    say: "Click Save when the name is ready."
    do:
      - click: {role: button, name: Save}
        at: "Save"
      - click: {role: button, name: Other}
  - id: two
    say: "Open the run and mark the case."
    do:
      - click: {role: button, name: Open}
      - click: {role: button, name: Mark}
  - id: held
    say: "Hold this frame."
    do:
      - hold: 2.0
""",
    )
    tracks = {step.id: estimate_words(step.say) for step in lesson.steps}
    scheduled = schedule(lesson, tracks)
    anchored = scheduled["actions"][0]
    assert abs(anchored["press"] - anchored["anchor_t"]) == pytest.approx(0.08, abs=0.001)
    assert abs((anchored["press"] - anchored["anchor_t"]) - lesson.lead) < 1e-6
    loose = [item for item in scheduled["actions"] if item["step"] == "two"]
    assert loose[0]["press"] < loose[1]["press"]
    span = loose[1]["press"] - loose[0]["press"]
    assert span > 0.2
    by_id = {step["id"]: step for step in scheduled["steps"]}
    for step in scheduled["steps"]:
        assert step["audio_end"] <= step["t1"] + 1e-6
        tail = step["t1"] - max(step["audio_end"], max((item["press"] for item in step["actions"]), default=step["t0"]))
        if step["id"] == "held":
            assert tail >= 2.0
        else:
            assert tail <= 0.8
    assert by_id["one"]["t1"] <= by_id["two"]["t0"] + 1e-6


def test_prerecorded_clip_uses_the_sidecar_then_even_slices(tmp_path):
    wav = tmp_path / "line.wav"
    write_wav(str(wav), np.zeros(SR, np.float32))
    sidecar = Path(str(wav) + ".words.json")
    sidecar.write_text(json.dumps([
        {"word": "Click", "start": 0.1, "end": 0.4},
        {"word": "Next", "start": 0.5, "end": 0.9},
    ]), encoding="utf-8")
    words = align_clip(str(wav), "Click Next")
    assert words[1]["start"] == pytest.approx(0.5)
    sidecar.unlink()
    even = align_clip(str(wav), "Click Next", api_key="")
    assert [item["word"] for item in even] == ["Click", "Next"]
    assert even[0]["start"] == pytest.approx(0.0)
    assert even[-1]["end"] == pytest.approx(1.0, abs=0.05)


def test_elevenlabs_fetch_is_mocked_and_cached(tmp_path):
    lesson = _lesson(
        tmp_path,
        """
lesson: voice
title: Voice
app: {base_url: https://example.test}
voice: {provider: elevenlabs, voice_id: 7EzWGsX10sAS4c9m9cPf, model: eleven_v3}
steps:
  - id: next
    say: "Click Next."
    do:
      - hold: 0.2
""",
    )
    calls = {"n": 0}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            payload = {
                "audio_base64": base64.b64encode(b"not-audio").decode("ascii"),
                "alignment": {
                    "characters": list("Click Next."),
                    "character_start_times_seconds": [i * 0.1 for i in range(11)],
                    "character_end_times_seconds": [(i + 1) * 0.1 for i in range(11)],
                },
            }
            return json.dumps(payload).encode("utf-8")

    def opener(request, timeout=60):
        calls["n"] += 1
        assert "mp3_44100_192" in request.full_url
        assert request.get_header("Xi-api-key") == "test-key"
        body = json.loads(request.data.decode("utf-8"))
        assert body["text"] == "Click Next."
        return Response()

    os.environ["ELEVENLABS_API_KEY"] = "test-key"
    try:
        first = prepare_tracks(lesson, str(tmp_path / "cache"), opener=opener)
        second = prepare_tracks(lesson, str(tmp_path / "cache"), opener=opener)
    finally:
        os.environ.pop("ELEVENLABS_API_KEY", None)
    assert calls["n"] == 1
    assert first["tracks"]["next"][1]["word"].strip(".,") == "Next"
    assert second["tracks"]["next"][1]["start"] == first["tracks"]["next"][1]["start"]
    assert first["audio"]["next"]


def test_fetch_retries_then_raises(tmp_path):
    class Boom:
        def __enter__(self):
            raise OSError("offline")

        def __exit__(self, *args):
            return False

    with pytest.raises(Exception) as error:
        fetch_elevenlabs("Hello", "voice", "eleven_v3", "key", opener=lambda *a, **k: Boom(), attempts=1)
    assert error.value.code == "voice_unavailable"


def test_music_ducks_about_14_db_under_the_voice():
    voice = np.zeros(SR * 3, np.float32)
    voice[int(0.2 * SR): int(1.2 * SR)] = 0.2
    music = loop_bed(3.0)
    ducked = sidechain(music, voice, depth_db=14, attack=0.15, release=0.40)

    def rms(samples: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.square(samples))) + 1e-12)

    during = 20 * np.log10(rms(ducked[int(0.7 * SR): int(1.05 * SR)]) / rms(music[int(0.7 * SR): int(1.05 * SR)]))
    after = 20 * np.log10(rms(ducked[int(2.6 * SR): int(2.95 * SR)]) / rms(music[int(2.6 * SR): int(2.95 * SR)]))
    assert during == pytest.approx(-14, abs=1.0)
    assert after > -3.0
    assert np.max(np.abs(click_sample())) <= 1.0
    assert key_sample().size < click_sample().size


def test_loudnorm_hits_the_target(tmp_path):
    rng = np.random.default_rng(3)
    voice = np.zeros(SR * 6, np.float32)
    voice[int(0.3 * SR): int(5.2 * SR)] = rng.uniform(-0.25, 0.25, int(4.9 * SR)).astype(np.float32)
    mixed = np.clip(loop_bed(6.0) * 0.6 + voice, -1, 1)
    wav = tmp_path / "mix.wav"
    write_wav(str(wav), mixed)
    report = loudnorm(str(wav), "", str(tmp_path / "out.m4a"))
    assert report["lufs"] == pytest.approx(-14, abs=0.5)
    assert report["true_peak"] <= -1.0
    check = _check_loudness({"audio": report})
    assert check["ok"]


def test_captions_wrap_to_two_lines_and_drop_em_dashes(tmp_path):
    words = []
    cursor = 0.0
    for token in "Alpha beta gamma delta epsilon zeta eta theta iota kappa".split():
        words.append({"word": token, "start": cursor, "end": cursor + 0.3})
        cursor += 0.3
    words.append({"word": "wait\u2014now", "start": cursor, "end": cursor + 0.3})
    cues = wrap_cues(words)
    problems = validate_cues(cues)
    assert problems == []
    assert all(len(line) <= 42 for cue in cues for line in cue["text"].split("\n"))
    assert all(cue["text"].count("\n") < 2 for cue in cues)
    assert "\u2014" not in " ".join(cue["text"] for cue in cues)
    srt = tmp_path / "captions.srt"
    vtt = tmp_path / "captions.vtt"
    write_srt(cues, str(srt))
    write_vtt(cues, str(vtt))
    assert srt.read_text(encoding="utf-8").startswith("1\n")
    assert vtt.read_text(encoding="utf-8").startswith("WEBVTT")


def test_cursor_fades_out_after_two_seconds_idle_and_back_in():
    travel = plan_travel(Point(0, 0), Point(100, 0), press=1.0, index=0)
    later = plan_travel(Point(100, 0), Point(200, 0), press=travel.release + 4.0, index=1)
    assert cursor_opacity([travel], travel.release + 1.0) == pytest.approx(1.0)
    assert cursor_opacity([travel], travel.release + 2.2) < 0.5
    assert cursor_opacity([travel], travel.release + 2.4) == pytest.approx(0.0, abs=0.05)
    hidden = travel.release + 2.45
    assert hidden < later.t0 - 0.25
    assert cursor_opacity([travel, later], hidden) == pytest.approx(0.0, abs=0.05)
    assert cursor_opacity([travel, later], later.t0 - 0.05) > 0.7


def test_frozen_check_fails_a_still_and_passes_motion(tmp_path):
    import subprocess

    still = tmp_path / "still.mp4"
    moving = tmp_path / "moving.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=3", "-r", "10", str(still)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=3,noise=alls=40:allf=t", "-r", "10", str(moving)],
        check=True,
    )
    assert _check_frozen(str(still))["ok"] is False
    assert _check_frozen(str(moving))["ok"] is True
    assert _check_sync({"retime": {"presses": [{"slip": 0.05}]}})["ok"] is True
    assert _check_sync({"retime": {"presses": [{"slip": 0.09}]}})["ok"] is False


def test_full_frame_render_rate_covers_a_90s_lesson(tmp_path):
    source = np.full((1080, 1920, 3), 30, np.uint8)
    source[::20, :] = (180, 180, 200)
    source[:, ::20] = (180, 180, 200)
    camera = build_camera_track(
        [Shot(1.2, 700, 400, 1.4, "click")],
        duration=3.0,
        fps=60,
        source_size=(1920, 1080),
    )
    travel = plan_travel(Point(200, 200), Point(700, 400), press=1.2, index=0)
    movie = render_movie(
        lambda _t: source,
        (1920, 1080),
        [travel],
        camera,
        [{"output": 1.2, "x": 700, "y": 400}],
        str(tmp_path / "full.mp4"),
        motion_blur="cursor",
        cursor_scale=1.6,
    )
    assert movie["width"] == 1920 and movie["height"] == 1080 and movie["fps"] == 60
    assert movie["supersamples"] > 0
    # 3 s of 1080p60. 45 fps puts a 90 s lesson under 2 min.
    assert movie["elapsed_s"] <= 3.0 * (120 / 90)
    preview_camera = build_camera_track(
        [Shot(1.2, 700, 400, 1.4, "click")],
        duration=4.0,
        fps=30,
        source_size=(1920, 1080),
    )
    preview = render_movie(
        lambda _t: source,
        (1920, 1080),
        [travel],
        preview_camera,
        [{"output": 1.2, "x": 700, "y": 400}],
        str(tmp_path / "preview.mp4"),
        preview=True,
        motion_blur="off",
    )
    assert preview["width"] == 960 and preview["height"] == 540 and preview["fps"] == 30
    # 4 s of preview. 90 fps puts a 90 s preview under 30 s.
    assert preview["elapsed_s"] <= 4.0 * (30 / 90)
