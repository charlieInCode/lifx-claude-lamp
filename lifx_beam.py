#!/usr/bin/env python3
"""
lifx_beam.py — drive a LIFX Beam (or any LIFX light) over the LAN protocol.

Used as a Claude Code hook to turn the lamp into a physical status indicator:

    idle    solid warm mango  — Claude is done / waiting for you
    working pulsing cyan       — Claude is processing
    input   solid purple       — Claude needs permission / your input
    off      lamp off          — session ended

It speaks the LIFX LAN protocol (binary UDP on port 56700). It broadcasts a
"tagged" packet, which every LIFX device on the local subnet obeys — so there is
no device discovery and no hardcoded IP. The light's own firmware runs the
"working" pulse animation, so this script just fires a packet and exits (no
daemon needed).

Usage:
    python3 lifx_beam.py <state>      where state is: idle | working | input | off

Optional env vars:
    LIFX_BROADCAST   broadcast address (default 255.255.255.255)
    LIFX_LABEL       if set, target only the light with this label (requires a
                     discovery round-trip; leave unset to broadcast to all lights)
"""

import os
import socket
import struct
import sys
import time

LIFX_PORT = 56700
SOURCE = 0x4C414D50  # "LAMP"

CACHE_PATH = os.environ.get(
    "LIFX_CACHE", os.path.expanduser("~/.claude/lifx_hooks/lights.json")
)


def primary_ip():
    """This host's primary IPv4, or None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def load_cache():
    """Previously discovered LIFX light IPs (written by `scan`)."""
    try:
        import json
        with open(CACHE_PATH) as f:
            return list(json.load(f))
    except (OSError, ValueError):
        return []


def broadcast_addrs():
    """Return broadcast addresses to target.

    macOS will not route to 255.255.255.255 without a bound interface, so we
    derive the subnet-directed broadcast (e.g. 192.168.1.255) from this host's
    primary IP and assume a /24. Override with LIFX_BROADCAST=a.b.c.255 if your
    network uses a different mask.
    """
    override = os.environ.get("LIFX_BROADCAST")
    if override:
        return [override]
    addrs = []
    local_ip = primary_ip()
    if local_ip:
        parts = local_ip.split(".")
        parts[3] = "255"
        addrs.append(".".join(parts))
    addrs.append("255.255.255.255")  # fallback
    return addrs

# LIFX message type IDs
SET_POWER = 117      # LightSetPower: level(u16), duration(u32 ms)
SET_COLOR = 102      # SetColor: reserved(u8), HSBK(4xu16), duration(u32 ms)
SET_WAVEFORM = 103   # SetWaveform: pulse/breathe animation run by the firmware


def _hsbk(deg, sat, bri, kelvin=3500):
    """Convert friendly values to LIFX 16-bit HSBK."""
    h = int(deg / 360.0 * 65535) & 0xFFFF
    s = int(max(0.0, min(1.0, sat)) * 65535)
    b = int(max(0.0, min(1.0, bri)) * 65535)
    return h, s, b, kelvin


def build_packet(pkt_type, payload=b"", tagged=True, target=0):
    """Assemble a 36-byte LIFX header + payload."""
    size = 36 + len(payload)
    # frame: protocol(12 bits)=1024, addressable bit=12, tagged bit=13, origin=0
    flags = 1024 | (1 << 12)
    if tagged:
        flags |= (1 << 13)
    frame = struct.pack("<HHI", size, flags, SOURCE)
    # frame address: target(8) + reserved(6) + response flags(1) + sequence(1)
    frame_addr = struct.pack("<Q", target) + b"\x00" * 6 + struct.pack("<BB", 0, 0)
    # protocol header: reserved(8) + type(2) + reserved(2)
    proto = struct.pack("<QHH", 0, pkt_type, 0)
    return frame + frame_addr + proto + payload


def set_power(level, duration=400):
    return build_packet(SET_POWER, struct.pack("<HI", level, duration))


def set_color(deg, sat, bri, kelvin=3500, duration=500):
    h, s, b, k = _hsbk(deg, sat, bri, kelvin)
    return build_packet(SET_COLOR, struct.pack("<BHHHHI", 0, h, s, b, k, duration))


def set_waveform(deg, sat, bri, kelvin=3500, period=1600, cycles=1e9,
                 skew=0, waveform=1, transient=1):
    # waveform 1 = SINE (smooth breathe). transient=1 returns to base color.
    h, s, b, k = _hsbk(deg, sat, bri, kelvin)
    payload = struct.pack("<BBHHHHIfhB", 0, transient, h, s, b, k,
                          period, cycles, skew, waveform)
    return build_packet(SET_WAVEFORM, payload)


def multizone_off():
    """SetMultiZoneEffect type=OFF — stop any firmware Move/scene effect on a
    multizone device (the Beam) so a single solid color actually sticks."""
    payload = struct.pack("<IBHIQII", 0, 0, 0, 0, 0, 0, 0) + b"\x00" * 32
    return build_packet(508, payload)


# State -> ordered list of packets to send.
STATES = {
    # warm sunset mango, solid
    "idle":    lambda: [multizone_off(), set_power(65535),
                        set_color(28, 0.85, 0.55, 2700, duration=0)],
    # pure cyan breathe: instant cyan base, then a brightness-only pulse (same
    # hue/sat/kelvin) so no other colors appear. The firmware loops it.
    "working": lambda: [multizone_off(), set_power(65535),
                        set_color(200, 1.0, 0.45, 6500, duration=0),
                        set_waveform(200, 1.0, 1.0, 6500, period=2200)],
    # purple, solid — needs your attention
    "input":   lambda: [multizone_off(), set_power(65535),
                        set_color(285, 0.9, 0.7, 3500, duration=0)],
    # off
    "off":     lambda: [set_power(0)],
}


def send(packets):
    # Unicast to known light IPs (reliable even where the network drops
    # broadcast) AND broadcast (covers lights whose IP changed since the last
    # scan). Duplicates are harmless — LIFX commands are idempotent.
    targets = list(dict.fromkeys(load_cache() + broadcast_addrs()))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        for pkt in packets:
            # send each packet a couple of times — UDP, no retransmit
            for _ in range(2):
                for addr in targets:
                    try:
                        sock.sendto(pkt, (addr, LIFX_PORT))
                    except OSError:
                        pass
                time.sleep(0.02)
    finally:
        sock.close()


def discover(timeout=2.0):
    """Broadcast GetService and print any LIFX devices that answer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 0))
    sock.settimeout(0.4)
    get_service = build_packet(2, b"")  # type 2 = GetService
    for addr in broadcast_addrs():
        try:
            sock.sendto(get_service, (addr, LIFX_PORT))
        except OSError:
            pass
    found = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, src = sock.recvfrom(1024)
        except socket.timeout:
            continue
        found[src[0]] = len(data)
    sock.close()
    if found:
        for ip in found:
            print(f"  found LIFX device at {ip}")
    else:
        print("  no LIFX devices answered (check the light is on the same subnet)")
    return found


def scan(timeout=2.5):
    """Unicast-probe every host on the local /24 and cache LIFX responders.

    Works even where the network drops broadcast (e.g. wireless client
    isolation). Writes the found IPs to CACHE_PATH so plain state changes can
    unicast to them. Re-run if a light's DHCP address changes.
    """
    import json
    ip = primary_ip()
    if not ip:
        print("could not determine local IP")
        return 0
    base = ip.rsplit(".", 1)[0] + "."
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    sock.settimeout(0.3)
    get_service = build_packet(2, b"")  # type 2 = GetService
    print(f"scanning {base}1-254 for LIFX lights...")
    for i in range(1, 255):
        try:
            sock.sendto(get_service, (base + str(i), LIFX_PORT))
        except OSError:
            pass
    found = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _, src = sock.recvfrom(1024)
            found.add(src[0])
        except socket.timeout:
            continue
    sock.close()
    found = sorted(found)
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(found, f)
    if found:
        for f_ip in found:
            print(f"  found LIFX light at {f_ip}")
        print(f"cached {len(found)} light(s) to {CACHE_PATH}")
    else:
        print("  no LIFX lights found (is the light on and on this subnet?)")
    return 0


def main():
    state = sys.argv[1] if len(sys.argv) > 1 else "idle"
    if state == "scan":
        return scan()
    if state == "discover":
        print(f"broadcasting to: {', '.join(broadcast_addrs())}")
        discover()
        return 0
    builder = STATES.get(state)
    if builder is None:
        sys.stderr.write(f"unknown state: {state}\n")
        return 1
    send(builder())
    return 0


if __name__ == "__main__":
    sys.exit(main())
