"""Xvfb display and 60 fps x11grab on one monotonic clock."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Any


class Display:
    def __init__(self, name: str, process: subprocess.Popen, width: int, height: int):
        self.name = name
        self.process = process
        self.width = width
        self.height = height

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class Capture:
    def __init__(self, process: subprocess.Popen, path: str, started_mono: float, fps: int, width: int, height: int):
        self.process = process
        self.path = path
        self.started_mono = started_mono
        self.first_frame_mono: float | None = None
        self.fps = fps
        self.width = width
        self.height = height
        self._stderr = process.stderr
        self._stderr_lines: list[str] = []
        self._pts_seen = 0.0
        self._thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._thread.start()

    def _read_stderr(self) -> None:
        stream = self._stderr
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", errors="replace")
            self._stderr_lines.append(line)
            self._note(line)

    def _note(self, line: str) -> None:
        # showinfo lines arrive late for the first burst, then live.
        # seen - pts_time settles on the monotonic time of pts 0.
        match = re.search(r"pts_time:([0-9.]+)", line)
        if not match:
            return
        pts = float(match.group(1))
        estimate = time.monotonic() - pts
        if estimate < self.started_mono - 0.05:
            return
        if self.first_frame_mono is None or estimate < self.first_frame_mono:
            self.first_frame_mono = estimate
        self._pts_seen = max(self._pts_seen, pts)

    def wait_for_clock(self, timeout: float = 2.0) -> float:
        """Block until the grabber clock is known, then return pts-0 on CLOCK_MONOTONIC."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.first_frame_mono is not None and self._pts_seen >= 0.25:
                return self.first_frame_mono
            if self.process.poll() is not None:
                break
            time.sleep(0.02)
        return self.first_frame_mono or self.started_mono

    def stop(self) -> dict[str, Any]:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._thread.join(timeout=2)
        stderr = "".join(self._stderr_lines)
        return {
            "clock": "CLOCK_MONOTONIC",
            "started_mono": self.started_mono,
            "first_frame_mono": self.first_frame_mono or self.started_mono,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "path": self.path,
            "stderr_tail": "\n".join(stderr.splitlines()[-20:]),
        }


def start_xvfb(display: str = ":90", width: int = 1920, height: int = 1080) -> Display:
    # A private screen. The agent's shared display is left alone.
    process = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp", "-ac"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Xvfb {display} exited")
        probe = subprocess.run(
            ["xdpyinfo", "-display", display],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            return Display(display, process, width, height)
        time.sleep(0.05)
    process.kill()
    raise RuntimeError(f"Xvfb {display} did not start")


def start_capture(display: str, path: str, fps: int = 60, width: int = 1920, height: int = 1080) -> Capture:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # stdbuf keeps showinfo line-buffered so the first-frame clock is live.
    prefix = ["stdbuf", "-oL", "-eL"] if shutil.which("stdbuf") else []
    command = prefix + [
        "ffmpeg", "-y", "-nostats",
        "-f", "x11grab",
        "-draw_mouse", "0",
        "-framerate", str(fps),
        "-video_size", f"{width}x{height}",
        "-i", f"{display}+0,0",
        "-an",
        "-vf", "showinfo",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "ultrafast",
        path,
    ]
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return Capture(process, path, started, fps, width, height)


def drop_ratio(frame_count: float, duration: float, fps: float) -> float:
    expected = duration * fps
    if expected <= 0:
        return 0.0
    return abs(frame_count - expected) / expected
