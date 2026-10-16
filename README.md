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

## Grok

The recorder is the Linux machine that shows the product. Grok Bot's cloud computer and a local computer are different machines. Record on the one whose display you can click. Python 3 is the only dependency. Copy the `linux/` directory and run it. There is no install step.

Three ways to call the same commands:

**Local.** `python3 linux/screenreel doctor`, then `record start`, `mark`, and `record stop`.

**MCP.** Grok Build starts a stdio server from `.grok/config.toml` when the session is in this repo. A local Grok Bot shell uses the same process: `python3 linux/screenreel serve`. For a Grok custom connector, or any client that needs a URL, serve streamable HTTP on the recording machine:

```bash
SCREENREEL_TOKEN=secret python3 linux/screenreel serve --http 127.0.0.1:8765
```

The MCP path is `/mcp`. A public address is refused until `SCREENREEL_TOKEN` is set. Give Grok that URL and the bearer token. Streamable HTTP works through a Cloudflare quick tunnel.

**API.** The xAI Responses API can call that URL directly:

```json
{
  "type": "mcp",
  "server_url": "https://<tunnel>/mcp",
  "server_label": "screenreel",
  "authorization": "<SCREENREEL_TOKEN>"
}
```

`GET /v1/tools` returns function definitions in the Responses API shape. `POST /v1/call` with `{"name":"doctor","arguments":{}}` runs one tool and returns its JSON. Send that JSON back as the function output. Those calls run on the recording machine, not on xAI's servers.

`plan`, `beautify`, `frames`, and `timeline` still need the Mac `screenreel` binary. The Linux server returns that in JSON when the binary is not on `PATH`.

`grok inspect` lists the screenreel skill, agent, and MCP server. The skill is `.grok/skills/screenreel`.
