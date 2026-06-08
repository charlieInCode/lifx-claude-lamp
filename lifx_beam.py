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
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
        local_ip = s.getsockname()[0]
        s.close()
        parts = local_ip.split(".")
        parts[3] = "255"
        addrs.append(".".join(parts))
    except OSError:
        pass
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


# State -> ordered list of packets to send.
STATES = {
    # warm sunset mango, solid
    "idle":    lambda: [set_power(65535), set_color(28, 0.85, 0.55, 2700)],
    # cyan breathe — the firmware loops this until the next state is sent
    "working": lambda: [set_power(65535), set_color(200, 0.7, 0.35, 4000, 200),
                        set_waveform(200, 0.85, 0.95, 6500)],
    # purple, solid — needs your attention
    "input":   lambda: [set_power(65535), set_color(285, 0.9, 0.7, 3500)],
    # off
    "off":     lambda: [set_power(0)],
}


def send(packets):
    targets = broadcast_addrs()
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


def main():
    state = sys.argv[1] if len(sys.argv) > 1 else "idle"
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
