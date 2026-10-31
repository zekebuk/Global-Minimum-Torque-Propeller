# Connecting Claude to DaVinci Resolve Studio (MCP)

## How it works

The Resolve MCP server ([samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp)) is a
local Python process. It talks to Resolve through Blackmagic's scripting API, the same API the scripts in this repo use.
So the server has to run **on the computer where Resolve is running**, launched by a local Claude client
(Claude Desktop or the Claude Code CLI). A cloud session can't reach it.

Because both use the same API, `scripts/doctor.py` is a good diagnostic. If it connects, the MCP server can too.

## Setup (in order)

1. **Resolve Studio open**, with a project open.
2. Preferences ▸ System ▸ General ▸ **External scripting using: Local**. Then **restart Resolve**.
3. Python: a **64-bit** CPython **3.10–3.12** is the safe range. Older Resolve builds fail on 3.12+ (they import the removed
   `imp` module), and some fail on 3.13+.
4. Run the diagnostic with that same Python:
   ```bash
   python3 scripts/doctor.py
   ```
5. Install the MCP server:
   ```bash
   npx davinci-resolve-mcp setup     # managed install + venv, detects Resolve, can configure Claude clients
   # or
   git clone https://github.com/samuelgursky/davinci-resolve-mcp && cd davinci-resolve-mcp && python install.py
   ```
6. If something's off, run the server's own diagnostic: `python3 scripts/doctor.py` inside the davinci-resolve-mcp folder.
7. Register it with your client if setup didn't do it already:
   - **Claude Code:** `claude mcp add davinci-resolve -- /full/path/to/python /full/path/to/davinci-resolve-mcp/src/server.py`
   - **Claude Desktop:** add this to `claude_desktop_config.json`:
     ```json
     { "mcpServers": { "davinci-resolve": {
         "command": "/full/path/to/python",
         "args": ["/full/path/to/davinci-resolve-mcp/src/server.py"] } } }
     ```
     Restart Claude Desktop afterwards.

## "It starts but stays disconnected": the usual causes

| Symptom | Cause | Fix |
|---|---|---|
| `scriptapp("Resolve")` returns None | External scripting is not set to Local, or Resolve wasn't restarted after changing it | Set it to Local and restart Resolve |
| Works in a terminal, not in Claude | The client launches a **different Python** | Put the exact interpreter path printed by `doctor.py` in the MCP config |
| Import error for `imp` | Python 3.12+ on an older Resolve build | Use a 3.11 venv |
| macOS: Resolve can't find Python | Resolve only looks in `PYTHON3HOME` or `/usr/local/bin/python3` | `launchctl setenv PYTHON3HOME "$(python3 -c 'import sys;print(sys.prefix)')"`, then restart Resolve (doesn't survive a reboot) |
| Connects, but reports the free version | Both editions are installed and the free one is open | Open the Studio app |
| Resolve 21.1 free: no Python scripts | 21.1 moved Python scripting to Studio | Use Studio |

## What to ask Claude once it's connected

- "Open project FPV_2026-10-31_quarry, list the red markers on the REEL_selects timeline, and trim each clip to marker ±0.5 s."
- "Apply the base grade DRX to every clip on V1 of YT_final and render both deliverables."
- "Add timeline markers at every downbeat from track.beats.json."

The scripts in `scripts/` cover the repeatable parts **without** MCP, so the pipeline works today. MCP is for the
interactive, per-video tweaks.
