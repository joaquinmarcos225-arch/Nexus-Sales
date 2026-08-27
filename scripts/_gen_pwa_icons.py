"""Genera iconos PWA simples (rojo sobre negro)."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _png(size: int) -> bytes:
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            cx, cy = size / 2, size / 2
            r1 = size * 0.22
            r2 = size * 0.08
            dx, dy = x + 0.5 - cx, y + 0.5 - cy
            d = (dx * dx + dy * dy) ** 0.5
            if d <= r2:
                row += bytes((255, 255, 255, 255))
            elif d <= r1:
                row += bytes((220, 38, 38, 255))
            else:
                row += bytes((12, 6, 6, 255))
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def main() -> None:
    blob_192 = _png(192)
    blob_512 = _png(512)
    for folder in (ROOT / "frontend" / "public", ROOT / "nexus-support" / "public"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pwa-192.png").write_bytes(blob_192)
        (folder / "pwa-512.png").write_bytes(blob_512)
        print("wrote", folder)


if __name__ == "__main__":
    main()
