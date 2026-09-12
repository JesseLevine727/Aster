#!/usr/bin/env python3
"""Convert a flat binary into one little-endian 32-bit word per line."""

from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} INPUT.BIN OUTPUT.HEX", file=sys.stderr)
        return 2

    source = Path(sys.argv[1]).read_bytes()
    if len(source) % 4:
        source += b"\x00" * (4 - (len(source) % 4))

    destination = Path(sys.argv[2])
    with destination.open("w", encoding="ascii") as output:
        for offset in range(0, len(source), 4):
            word = int.from_bytes(source[offset : offset + 4], "little")
            output.write(f"{word:08x}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
