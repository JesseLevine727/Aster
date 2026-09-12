#!/usr/bin/env python3
"""Convert an ELF ROM image or flat binary into ROM initialization words."""

import argparse
from pathlib import Path
import struct
import sys


def elf_to_image(source: bytes, rom_bytes: int) -> bytearray:
    if source[:4] != b"\x7fELF":
        image = bytearray(source[:rom_bytes])
        if len(image) < rom_bytes:
            image.extend(b"\x00" * (rom_bytes - len(image)))
        return image

    if source[4] != 1 or source[5] != 1:
        raise ValueError("only little-endian ELF32 images are supported")

    header = struct.unpack_from("<16sHHIIIIIHHHHHH", source, 0)
    ph_offset = header[5]
    ph_entry_size = header[9]
    ph_count = header[10]
    image = bytearray(rom_bytes)

    for index in range(ph_count):
        entry_offset = ph_offset + index * ph_entry_size
        p_type, p_offset, _p_vaddr, p_paddr, p_filesz, _p_memsz, _p_flags, _p_align = struct.unpack_from(
            "<IIIIIIII", source, entry_offset
        )
        if p_type != 1 or p_filesz == 0:
            continue
        if p_paddr + p_filesz > rom_bytes:
            raise ValueError(
                f"load segment at 0x{p_paddr:08x} does not fit in the {rom_bytes}-byte ROM"
            )
        image[p_paddr : p_paddr + p_filesz] = source[p_offset : p_offset + p_filesz]

    return image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom-bytes", type=int, default=65536)
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()

    try:
        source = Path(args.input).read_bytes()
        image = elf_to_image(source, args.rom_bytes)
    except (OSError, ValueError) as error:
        print(f"elf_to_hex: {error}", file=sys.stderr)
        return 1

    destination = Path(args.output)
    with destination.open("w", encoding="ascii") as output:
        for offset in range(0, len(image), 4):
            word = int.from_bytes(image[offset : offset + 4], "little")
            output.write(f"{word:08x}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
