"""Run the Linux screenreel CLI and return its JSON."""

import json
import os
import subprocess
import sys


def script_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenreel")


def call(argv):
    process = subprocess.run(
        [sys.executable, script_path(), *argv],
        capture_output=True,
        text=True,
    )
    text = process.stdout.strip()
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "error": text or process.stderr.strip() or "screenreel returned no JSON",
        }
    if not isinstance(payload, dict):
        payload = {"ok": False, "error": "screenreel returned a non-object"}
    payload.setdefault("ok", process.returncode == 0)
    if process.returncode != 0 and payload.get("ok"):
        payload["ok"] = False
    return payload


def doctor():
    return call(["doctor"])


def record_start(take, crop=None, fps=None):
    argv = ["record", "start", "--take", take]
    if crop:
        argv.extend(["--crop", str(crop)])
    if fps:
        argv.extend(["--fps", str(int(fps))])
    return call(argv)


def mark(take, label):
    return call(["mark", "--take", take, "--label", label])


def record_stop(take):
    return call(["record", "stop", "--take", take])
