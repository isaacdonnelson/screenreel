"""Captions from word timings. At most two lines, 42 characters, no em dashes."""

from __future__ import annotations

import os
from typing import Any

EM_DASH = "\u2014"


def wrap_cues(words: list[dict[str, Any]], limit: int = 42) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    line = ""
    start = None
    end = None
    lines: list[str] = []
    cue_start = None
    cue_end = None

    def flush_line() -> None:
        nonlocal line, start, end, lines, cue_start, cue_end
        if not line:
            return
        lines.append(line)
        line = ""
        if len(lines) == 2:
            cues.append({"start": cue_start, "end": end, "text": "\n".join(lines)})
            lines = []
            cue_start = None
        start = None

    for word in words:
        token = str(word["word"]).replace(EM_DASH, "-")
        if EM_DASH in str(word["word"]):
            token = token
        piece = token if not line else f"{line} {token}"
        if len(piece) > limit and line:
            flush_line()
            piece = token
        if start is None:
            start = float(word["start"])
            if cue_start is None:
                cue_start = start
        line = piece
        end = float(word["end"])
        cue_end = end
    flush_line()
    if lines:
        cues.append({"start": cue_start, "end": cue_end, "text": "\n".join(lines)})
    return cues


def caption_text(cues: list[dict[str, Any]], t: float) -> str:
    for cue in cues:
        if float(cue["start"]) <= t <= float(cue["end"]) + 0.05:
            return str(cue["text"])
    return ""


def validate_cues(cues: list[dict[str, Any]]) -> list[str]:
    problems = []
    previous_end = -1.0
    for cue in cues:
        text = str(cue["text"])
        if EM_DASH in text:
            problems.append("em dash")
        for line in text.split("\n"):
            if len(line) > 42:
                problems.append(f"line longer than 42: {line}")
        if text.count("\n") > 1:
            problems.append("more than 2 lines")
        if float(cue["start"]) < previous_end - 0.02:
            problems.append("overlapping cues")
        previous_end = float(cue["end"])
    return problems


def _stamp(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def write_srt(cues: list[dict[str, Any]], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(f"{index}\n{_stamp(float(cue['start']))} --> {_stamp(float(cue['end']))}\n{cue['text']}\n")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(blocks))


def write_vtt(cues: list[dict[str, Any]], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = ["WEBVTT", ""]
    for cue in cues:
        start = _stamp(float(cue["start"])).replace(",", ".")
        end = _stamp(float(cue["end"])).replace(",", ".")
        lines.append(f"{start} --> {end}")
        lines.append(str(cue["text"]))
        lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
