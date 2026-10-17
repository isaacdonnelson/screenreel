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

Scripted lessons stay on Linux. `screenreel make lesson.yaml` drives a dedicated Chrome profile on its own Xvfb display (`:90`, 1920x1080), captures at 60 fps with the system cursor hidden, and renders the cursor, camera, and ripples in output time.

```bash
python3 -m pip install -r linux/requirements.txt
python3 linux/screenreel doctor
python3 linux/screenreel lint lessons/testfiesta-3-1.yaml
python3 linux/screenreel make lessons/testfiesta-3-1.yaml --plan-only --allow-estimated-voice
python3 linux/screenreel make lessons/testfiesta-3-1.yaml --preview
```

`TESTFIESTA_URL`, `TESTFIESTA_USER`, and `TESTFIESTA_PASSWORD` come from the environment. Log in once with `python3 linux/screenreel login --profile testfiesta` before a full make. `python3 -m pytest -q tests` runs the Linux checks. On a Mac, `make test` also runs `swift run screenreel-check`.

Each command prints one JSON object. On the Mac, Screen Recording and Accessibility must be allowed for the app that launches the binary. `doctor` names that app.

Start a Grok session with `grok --agent-profile screenreel`.
