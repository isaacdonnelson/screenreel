# screenreel

Records a screen and renders a demo from a JSON edit list. The system cursor is hidden. Clicks, cursor motion, scrolls, and key timing (not the characters) go to `events.jsonl`. `edit.json` holds the brand color, zooms, timed blurs, covers, speed segments, and cuts.

Record on the computer that can click. Render on the Mac. The take folder is the same in both places.

## Linux, including a Grok Bot computer

[Grok Bot](https://docs.x.ai/grok-bot/overview) keeps a persistent computer with a terminal and a display. Install the recorder there:

```bash
curl -fsSL https://raw.githubusercontent.com/isaacdonnelson/screenreel/main/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
screenreel doctor
```

On that computer the files live in `/workspace/screenreel` when `/workspace` is writable. That is the shared workspace described in [Use the computer and apps](https://docs.x.ai/grok-bot/computer-and-apps).

```bash
screenreel record start --take takes/demo
screenreel mark --take takes/demo --label "step 1: open the project"
screenreel record stop --take takes/demo
```

The same actions are available two other ways:

- MCP command, stdio. In the Bot's plugins, add a custom MCP command that runs `python3 /workspace/screenreel/linux/mcp_server.py`. Tools: `screenreel_doctor`, `screenreel_record_start`, `screenreel_mark`, `screenreel_record_stop`. A command server runs on the computer for that conversation, which is what [Team Bots](https://docs.x.ai/grok-bot/team-bots) describes. From a checkout of this repo, `grok mcp add screenreel -- python3 linux/mcp_server.py` matches [MCP servers](https://docs.x.ai/build/features/mcp-servers).
- Local API. `screenreel api` listens on `127.0.0.1:8765`. `GET /v1/doctor`, `POST /v1/record/start`, `POST /v1/mark`, `POST /v1/record/stop`. `POST /mcp` is the MCP HTTP endpoint. A connector at [grok.com/connectors](https://docs.x.ai/grok/connectors) needs a public HTTPS URL, so tunnel this process only if it is running on the display you are recording. [Tunneling](https://docs.x.ai/grok/connectors/custom-mcp-tunneling) covers that.

Copy the take to the Mac before `plan` and `beautify`.

## Mac

```bash
make install
screenreel doctor
screenreel plan --take ./takes/demo
screenreel beautify --take ./takes/demo
screenreel frames --take ./takes/demo --markers
screenreel timeline --take ./takes/demo
```

Each command prints one JSON object. On the Mac, Screen Recording and Accessibility must be allowed for the app that launches the binary. `doctor` names that app.

Start a Grok session with `grok --agent screenreel`.
