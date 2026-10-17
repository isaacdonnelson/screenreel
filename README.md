# screenreel

Records a screen and renders a demo from a JSON edit list. The system cursor is hidden. Clicks, cursor motion, scrolls, and key timing (not the characters) go to `events.jsonl`. `edit.json` holds the brand color, zooms, timed blurs, covers, speed segments, and cuts.

The Linux recorder and the Mac renderer share that folder. Record where the clicks happen. Render on the Mac.

```bash
make install
screenreel doctor
screenreel record start --take ./takes/demo
screenreel mark --take ./takes/demo --label "step 1: open the project"
screenreel record stop --take ./takes/demo
screenreel plan --take ./takes/demo
screenreel beautify --take ./takes/demo
screenreel frames --take ./takes/demo --markers
screenreel timeline --take ./takes/demo
```

On Linux, `linux/screenreel` records and marks. Copy that take to the Mac before `plan` and `beautify`.

Scripted lessons stay on Linux. One command, `screenreel make lesson.yaml`, drives a dedicated Chrome profile on its own Xvfb display (`:90`, 1920x1080), captures at 60 fps with the system cursor hidden, and writes a finished 1920x1080 video: eased cursor and camera, narration, ducked music, click and typing SFX, captions, and a QA report. The plan for that pipeline is `docs/phase2.md`.

```bash
python3 -m pip install -r linux/requirements.txt
python3 -m playwright install chromium
sudo apt-get install -y ffmpeg tesseract-ocr xvfb
python3 linux/screenreel doctor
python3 linux/screenreel lint lessons/testfiesta-3-1.yaml
python3 linux/screenreel make lessons/testfiesta-3-1.yaml --plan-only --allow-estimated-voice
python3 linux/screenreel make lessons/testfiesta-3-1.yaml --preview --allow-estimated-voice
python3 linux/screenreel make lessons/testfiesta-3-1.yaml
```

Environment:

- `TESTFIESTA_URL`, `TESTFIESTA_USER`, `TESTFIESTA_PASSWORD` fill the `${...}` placeholders in the lesson. Never write those values into the file.
- `ELEVENLABS_API_KEY` turns on real speech (voice `7EzWGsX10sAS4c9m9cPf`, model `eleven_v3`, cached under the take). Without the key, pass `--allow-estimated-voice`, or set `audio: path/to/clip.wav` on a step. A sibling `clip.wav.words.json` supplies word timings.
- `DISPLAY` is optional. `make` starts Xvfb `:90` when you do not pass `--display`.

Useful flags: `--preview` (960x540, 30 fps), `--only <step-id>` (writes `steps/<id>.mp4` and does not replace `out.mp4`), `--reuse-capture`, `--plan-only`. `make` prints one JSON object. `ok` is false when QA fails. `qa.checks` covers sync within 80 ms, a frozen stretch over 1.5 s, loudness (-14 LUFS, true peak at most -1 dBTP), captions, and tesseract hits on `never_show`. Stills land in `contact.jpg`.

Log in once with `python3 linux/screenreel login --profile testfiesta` on the display you will reuse. If Chrome cannot see the page, confirm Xvfb is up and `google-chrome` is on `PATH`. If render progress sits still, the encoder is catching up; a 90 s 1080p60 lesson should finish rendering in about two minutes on a CPU with no GPU. `python3 -m pytest -q tests` runs the Linux checks. On a Mac, `make test` also runs `swift run screenreel-check`.

Each command prints one JSON object. On the Mac, Screen Recording and Accessibility must be allowed for the app that launches the binary. `doctor` names that app.

Start a Grok session with `grok --agent-profile screenreel`.
