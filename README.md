# LIFX Beam status lamp for Claude Code

Turns a LIFX Beam (or any LIFX light) into a physical status indicator for
Claude Code, using the **LIFX LAN protocol** (binary UDP on port 56700). No
cloud account, no API token, no internet — everything stays on your network.

| State    | Lamp                | Triggered when                         |
|----------|---------------------|----------------------------------------|
| idle     | solid green         | session starts / Claude finishes a turn |
| working  | pulsing cyan        | you submit a prompt / a tool runs      |
| input    | solid warm mango    | Claude needs permission or your input  |
| off       | lamp off            | session ends                           |

The lamp's own firmware runs the "working" pulse, so each hook just fires a UDP
packet and exits — no daemon. Hooks are configured `async`, so they never delay
Claude.

## How it works

`lifx_beam.py scan` unicast-probes every host on your `/24` and caches the IPs
of any LIFX lights that answer (to `~/.claude/lifx_hooks/lights.json`). State
changes then unicast to those cached IPs **and** broadcast a *tagged* packet.
Unicast is what makes this reliable on networks that drop broadcast between
clients (common with wireless client isolation); broadcast covers the case
where a light's DHCP address changed since the last scan. No hardcoded IPs, so
the same files work on multiple machines — each just runs `scan` once.

## Install (run on each Mac)

```bash
git clone <your-repo-url> lifx-claude-lamp
cd lifx-claude-lamp
./install.sh
```

`install.sh` copies `lifx_beam.py` to `~/.claude/lifx_hooks/`, merges the hooks
in `hooks.json` into `~/.claude/settings.json` (backing it up first, and
idempotently — safe to re-run), and runs a network scan to cache your light's
IP. Restart Claude Code afterwards (or open `/hooks` once) so it loads the new
hooks.

## Test manually

```bash
python3 lifx_beam.py scan       # find LIFX lights and cache their IPs
python3 lifx_beam.py idle       # warm mango
python3 lifx_beam.py working    # cyan breathe
python3 lifx_beam.py input      # purple
python3 lifx_beam.py off        # off
python3 lifx_beam.py discover   # broadcast-only probe (diagnostic)
```

## Requirements & gotchas

- **Python 3** (standard library only — no `pip install`).
- **The Mac and the Beam must be on the same subnet.** If `scan` finds nothing,
  the light is likely on a different network/VLAN (common when a router puts
  2.4 GHz / IoT devices on a separate range or a guest network). Put both on
  the same network and re-run `scan`.
- **Broadcast is often dropped** by wireless client isolation — that's why we
  scan and unicast. If `discover` (broadcast-only) finds nothing but `scan`
  does, this is why, and everything still works.
- Non-`/24` networks: `scan` probes the local `/24`. For other masks, run a
  scan from a host on the light's `/24`, or set `LIFX_BROADCAST` to the light's
  IP for a quick one-off (`LIFX_BROADCAST=192.168.50.74 python3 lifx_beam.py idle`).
- macOS may prompt to allow incoming connections for `scan`/`discover` (Python
  needs to receive replies); plain state changes only send, so they work
  regardless.
- Re-run `scan` if a light's DHCP address changes (or set a DHCP reservation).

## Two machines, one lamp

Both Macs broadcast to the same lamp; whichever fired most recently wins
(last-writer-wins). If you actively code on both at once and the color flips
around, that's expected — open an issue / extend with a priority scheme if it
bothers you.

## Uninstall

Remove the `lifx_beam.py` entries from the `hooks` block in
`~/.claude/settings.json` (a `.bak` is saved on install), and delete
`~/.claude/lifx_hooks/`.
