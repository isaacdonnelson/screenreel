"""Synchronous mark timestamps and a line cursor for the inbox.

The recorder used to stamp a mark when it drained the inbox, and it advanced
the cursor to the file size measured after the read. A mark written between
those two calls was skipped, and the last mark was dropped on stop.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any


def marker_line(label: str, mono: float) -> str:
    payload = {
        "label": label,
        "mono": round(float(mono), 6),
        "t": round(float(mono), 6),
        "type": "marker",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def append_mark(path: str, label: str, mono: float | None = None) -> float:
    stamp = time.monotonic() if mono is None else float(mono)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(marker_line(label, stamp))
        handle.flush()
        os.fsync(handle.fileno())
    return stamp


def drain_inbox(
    path: str,
    offset: int,
    started_mono: float,
    drain_mono: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Read complete lines from offset. A partial trailing line stays unread.

    The returned offset is the byte after the last complete line in this
    snapshot, not the file size at a later moment.
    """
    if not os.path.exists(path):
        return [], offset
    with open(path, "rb") as handle:
        handle.seek(max(0, offset))
        data = handle.read()
    if not data:
        return [], offset
    # Drop a trailing partial line so a concurrent append is not split.
    if not data.endswith(b"\n"):
        newline = data.rfind(b"\n")
        if newline < 0:
            return [], offset
        data = data[: newline + 1]
    events: list[dict[str, Any]] = []
    for raw in data.splitlines():
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        label = payload.get("label")
        if not label:
            continue
        mono = payload.get("mono")
        if mono is None:
            mono = drain_mono if drain_mono is not None else started_mono
        events.append({
            "t": round(float(mono) - float(started_mono), 3),
            "type": "marker",
            "label": label,
        })
    return events, offset + len(data)
