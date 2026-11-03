#!/usr/bin/env python3
"""Generate the placeholder artwork used by the bundled demo dataset.

Pure stdlib (zlib + struct) so it runs anywhere - no Pillow required.

    python tools/make_demo_assets.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "instascrapper" / "web" / "static" / "demo"
SIZE = 460

PALETTES = [
    ((250, 206, 52), (238, 42, 123), (98, 40, 215)),
    ((14, 165, 233), (56, 189, 248), (13, 148, 136)),
    ((251, 146, 60), (244, 63, 94), (120, 20, 80)),
    ((139, 92, 246), (236, 72, 153), (30, 27, 75)),
    ((34, 197, 94), (163, 230, 53), (6, 78, 59)),
    ((248, 113, 113), (253, 224, 71), (129, 140, 248)),
    ((15, 23, 42), (56, 189, 248), (232, 121, 249)),
    ((253, 186, 116), (251, 113, 133), (91, 33, 182)),
    ((45, 212, 191), (59, 130, 246), (17, 24, 39)),
    ((240, 171, 252), (129, 140, 248), (30, 64, 175)),
]


def _noise(x: int, y: int, seed: int) -> float:
    """Cheap deterministic hash noise in [-1, 1]."""
    n = (x * 73856093) ^ (y * 19349663) ^ (seed * 83492791)
    n = (n ^ (n >> 13)) * 1274126177
    return ((n ^ (n >> 16)) & 0xFFFF) / 32768.0 - 1.0


def _mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def render(index: int) -> bytearray:
    c0, c1, c2 = PALETTES[index % len(PALETTES)]
    angle = (index * 37) % 360
    rad = math.radians(angle)
    dx, dy = math.cos(rad), math.sin(rad)

    blobs = []
    for blob in range(4):
        bx = (math.sin(index * 2.7 + blob * 1.9) * 0.5 + 0.5) * SIZE
        by = (math.cos(index * 1.3 + blob * 2.3) * 0.5 + 0.5) * SIZE
        radius = SIZE * (0.18 + 0.12 * ((blob * 7 + index) % 5) / 5)
        strength = 0.55 + 0.35 * ((blob + index) % 3) / 3
        blobs.append((bx, by, radius, strength))

    rows = []
    half = SIZE / 2
    for y in range(SIZE):
        row = bytearray()
        for x in range(SIZE):
            t = ((x - half) * dx + (y - half) * dy) / (SIZE * 1.2) + 0.5
            t = min(1.0, max(0.0, t))
            colour = _mix(c0, c1, t * 1.6) if t < 0.62 else _mix(_mix(c0, c1, 1.0), c2, (t - 0.62) / 0.38)
            gain = 0.0
            for bx, by, radius, strength in blobs:
                dist = math.hypot(x - bx, y - by) / radius
                if dist < 2.0:
                    gain += strength * max(0.0, 1.0 - dist * 0.5) ** 2
            grain = _noise(x, y, index + 1) * 0.7
            for channel in range(3):
                value = colour[channel] + gain * (255 - colour[channel]) * 0.55 + grain
                row.append(min(255, max(0, int(value) // 3 * 3)))
        rows.append(row)
    return rows


def write_png(path: Path, rows: list[bytearray], width: int, height: int) -> None:
    raw = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for index in range(10):
        path = OUT / f"p{index + 1}.png"
        write_png(path, render(index), SIZE, SIZE)
        print(f"wrote {path.relative_to(OUT.parent.parent.parent.parent)} ({path.stat().st_size // 1024} KB)")

    # Avatar: same recipe, circular mask baked in.
    rows = render(9)
    for y in range(SIZE):
        for x in range(SIZE):
            dist = math.hypot(x - SIZE / 2, y - SIZE / 2) / (SIZE / 2)
            if dist > 1.0:
                rows[y][x * 3 : x * 3 + 3] = b"\x0e\x0e\x14"
            elif dist > 0.92:
                shade = 1.0 - (dist - 0.92) * 6
                for c in range(3):
                    rows[y][x * 3 + c] = int(rows[y][x * 3 + c] * shade)
    write_png(OUT / "avatar.png", rows, SIZE, SIZE)
    print("wrote avatar.png")


if __name__ == "__main__":
    main()
