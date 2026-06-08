# LIFX Beam status lamp for Claude Code

Turns a LIFX Beam (or any LIFX light) into a physical status indicator for
Claude Code, using the **LIFX LAN protocol** (binary UDP on port 56700). No
cloud account, no API token, no internet — everything stays on your network.

| State    | Lamp                | Triggered when                         |
|----------|---------------------|----------------------------------------|
| idle     | solid warm mango    | session starts / Claude finishes a turn |
| working  | pulsing cyan        | you submit a prompt / a tool runs      |
| input    | solid purple        | Claude needs permission or your input  |
| off       | lamp off            | session ends                           |

The lamp's own firmware runs the "working" pulse, so each hook just fires a UDP
packet and exits — no daemon. Hooks are configured `async`, so they never delay
Claude.

## How it works

`lifx_beam.py` broadcasts a *tagged* LIFX packet to the subnet-directed
broadcast address (e.g. `192.168.1.255`), which every LIFX light on the same
subnet obeys. There is no device discovery and no hardcoded IP, so the same
files work unchanged on multiple machines.

## Install (run on each Mac)

```bash
git clone <your-repo-url> lifx-claude-lamp
cd lifx-claude-lamp
./install.sh
```

`install.sh` copies `lifx_beam.py` to `~/.claude/lifx_hooks/`, merges the hooks
in `hooks.json` into `~/.claude/settings.json` (backing it up first, and
idempotently — safe to re-run), and runs a discovery test. Restart Claude Code
afterwards (or open `/hooks` once) so it loads the new hooks.

## Test manually

```bash
python3 lifx_beam.py discover   # list LIFX lights that answer on this subnet
python3 lifx_beam.py idle       # warm mango
python3 lifx_beam.py working    # cyan breathe
python3 lifx_beam.py input      # purple
python3 lifx_beam.py off        # off
```

## Requirements & gotchas

- **Python 3** (standard library only — no `pip install`).
- **The Mac and the Beam must be on the same subnet.** If `discover` finds
  nothing, the light is likely on a different network/VLAN (common when a
  router puts 2.4 GHz / IoT devices on a separate range or a guest network).
  Put both on the same network, or set `LIFX_BROADCAST` to the Beam's subnet
  broadcast address:
  ```bash
  LIFX_BROADCAST=192.168.1.255 python3 lifx_beam.py discover
  ```
- Non-`/24` networks: set `LIFX_BROADCAST` to the correct broadcast address.
- macOS may prompt to allow incoming connections for `discover` (Python needs
  to receive replies); plain state changes only send, so they work regardless.

## Two machines, one lamp

Both Macs broadcast to the same lamp; whichever fired most recently wins
(last-writer-wins). If you actively code on both at once and the color flips
around, that's expected — open an issue / extend with a priority scheme if it
bothers you.

## Uninstall

Remove the `lifx_beam.py` entries from the `hooks` block in
`~/.claude/settings.json` (a `.bak` is saved on install), and delete
`~/.claude/lifx_hooks/`.
