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

On Linux, `linux/screenreel` records and marks. Copy the take to the Mac before `plan` and `beautify`.

Each command prints one JSON object. On the Mac, Screen Recording and Accessibility must be allowed for the app that launches the binary. `doctor` names that app.

Start a Grok session with `grok --agent-profile screenreel`.
