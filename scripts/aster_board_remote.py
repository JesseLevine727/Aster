#!/usr/bin/env python3
"""On the PYNQ-Z1, as root: the Aster core's board run (milestone 18.7;
scripts/aster_board.py copies this, the bitstream and the program images, and
runs it). Self-contained: Python's standard library only.

It sets FCLK0 to 100 MHz (the IO PLL's 1000 MHz divided by 5 and 2,
recording the clock registers), loads aster_core.bit through the Linux FPGA
manager, checks the design's identity and measures its clock (a spinning
program's cycles over a second of wall time), then for each program of
manifest.json: holds the core in reset, zeroes main
memory and the register page (the CPU shell starts from zeroed memory), writes
the image, sets the tohost address, starts the core, and follows the run as
tb_aster_core_pynq.cpp does — until tohost is stored or, for a kernel, its
record line ends on the console after its window closed — then reads the
counters and holds the core again. report.json records each program and the
clock.

The PL is reached through /dev/mem: on this board's kernel, PL stores must be
32-bit mmap slice assignments (ctypes and struct.pack_into stores fault).

A design.json beside it (19.5: the Phase 19 SoC) gives the design's identity
instead of 18.7's: its magic and its main memory's bytes. For the Phase 20 SoC
(20.5: scripts/matrix_board.py) it also gives the SoC's configuration word
(0x3F05C, checked as the NPU's is) and page_bytes 0: the full SoC has no
register page to clear. Every program's report records the console's largest
lag behind the writer (tuning.md §7.1) and hart 1's live retired count (read,
not compared: hart 1 spins on after tohost).
"""
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import time

BASE, SIZE = 0x40000000, 0x40000
MAIN_BYTES, PAGE_BASE, PAGE_BYTES, CONSOLE, REGS = 0x20000, 0x20000, 0x4000, 0x30000, 0x3F000
MAGIC = 0x41535452                               # "ASTR": 18.7's design
NPU_CONFIG = None                                # 19.5: the NPU's configuration word (0x3F058), when given
SOC_CONFIG = None                                # 20.5: the Phase 20 SoC's configuration word (0x3F05C), when given
CONSOLE_BYTES = 4096                             # the console's ring (the Phase 20 SoC's: 16 KiB since 20.5)
CLOCK_MHZ = 100.0


class Mmio:
    def __init__(self, base, size):
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.map = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=base)
        self.words = memoryview(self.map).cast("I")      # (20.5: one 32-bit load a word, for the console's drain)

    def read_word(self, offset):
        return self.words[offset >> 2]

    def read(self, offset):
        return struct.unpack_from("<I", self.map, offset)[0]

    def read64(self, offset):
        return self.read(offset) | (self.read(offset + 4) << 32)

    def write(self, offset, value):
        self.map[offset:offset + 4] = struct.pack("<I", value & 0xFFFFFFFF)


def program_pl(bitstream):
    payload = bitstream.read_bytes()
    index = payload.find(b"\xaa\x99\x55\x66")
    if index < 0:
        raise RuntimeError("bitstream has no configuration sync word")
    body = payload[index:]
    swapped = b"".join(body[i:i + 4][::-1] for i in range(0, len(body) - len(body) % 4, 4))
    Path("/lib/firmware/aster_core.bin").write_bytes(swapped)
    Path("/sys/class/fpga_manager/fpga0/firmware").write_text("aster_core.bin")
    state = Path("/sys/class/fpga_manager/fpga0/state").read_text().strip()
    if state != "operating":
        raise RuntimeError(f"fpga_manager state is {state!r}, not operating")


def set_fclk0(target_mhz):
    """FPGA0_CLK_CTRL (0xF8000170): DIVISOR0 [13:8], DIVISOR1 [25:20], SRCSEL
    [5:4] (0x: the IO PLL); IO_PLL_CTRL (0xF8000108): FDIV [18:12], BYPASS
    [4] — the PYNQ-Z1's 50 MHz PS_CLK times 20 is 1000 MHz. Linux keeps the
    SLCR unlocked (the unlock is harmless); it is not locked again."""
    slcr = Mmio(0xF8000000, 0x1000)
    old, io_pll = slcr.read(0x170), slcr.read(0x108)
    if (old >> 4) & 0x3 not in (0, 1) or (io_pll >> 12) & 0x7F != 20 or io_pll & 0x10:
        raise RuntimeError(f"FCLK0 not from a 1000 MHz IO PLL: FPGA0_CLK_CTRL {old:#x}, IO_PLL_CTRL {io_pll:#x}")
    value = (old & ~((0x3F << 8) | (0x3F << 20))) | (5 << 8) | (2 << 20)
    slcr.write(0x008, 0xDF0D)
    slcr.write(0x170, value)
    time.sleep(0.05)
    readback = slcr.read(0x170)
    actual = 1000.0 / (((readback >> 8) & 0x3F) * ((readback >> 20) & 0x3F))
    if abs(actual - target_mhz) > 0.01:
        raise RuntimeError(f"FCLK0 is {actual} MHz, not {target_mhz}")
    return {"mhz": actual, "fpga0_clk_ctrl_before": old, "fpga0_clk_ctrl": readback, "io_pll_ctrl": io_pll}


def measure_clock(pl):
    """The design's clock, measured: a program that only spins (jal x0, 0),
    its cycle counter over about a second of wall time."""
    pl.write(REGS + 0x00, 0)
    pl.write(0, 0x0000006F)
    pl.write(REGS + 0x30, 0)
    pl.write(REGS + 0x00, 1)
    time.sleep(0.01)
    c0, t0 = pl.read64(REGS + 0x10), time.monotonic()
    time.sleep(1.0)
    c1, t1 = pl.read64(REGS + 0x10), time.monotonic()
    pl.write(REGS + 0x00, 0)
    return (c1 - c0) / (t1 - t0) / 1e6


def run_program(pl, entry, image):
    load_started = time.monotonic()
    pl.write(REGS + 0x00, 0)                     # hold the core
    for offset in range(0, MAIN_BYTES, 4):
        pl.write(offset, 0)
    for offset in range(0, PAGE_BYTES, 4):       # (none on the full Phase 20 SoC)
        pl.write(PAGE_BASE + offset, 0)
    data = image + b"\0" * (-len(image) % 4)
    for offset in range(0, len(data), 4):
        word = struct.unpack_from("<I", data, offset)[0]
        if word:
            pl.write(offset, word)
    for offset in range(0, len(data), 4):        # check the load, every word (19.5: odd words too)
        if pl.read(offset) != struct.unpack_from("<I", data, offset)[0]:
            raise RuntimeError(f"{entry['name']}: image read-back mismatch at {offset:#x}")
    pl.write(REGS + 0x30, entry["tohost"])
    started = time.monotonic()
    pl.write(REGS + 0x00, 1)
    loaded = time.monotonic()
    console, seen, status_word, lag = bytearray(), 0, 0, 0
    result = "TIMEOUT"
    limit = entry["max_cycles"] / (CLOCK_MHZ * 1e6) * 4 + 2   # seconds, with room for polling
    while time.monotonic() - started < limit:
        status_word = pl.read(REGS + 0x04)
        count = pl.read(REGS + 0x08)
        lag = max(lag, count - seen)
        if count - seen > CONSOLE_BYTES:             # (20.5: the program's failure, recorded; the run goes on)
            result = "CONSOLE_OVERFLOW"
            break
        while seen < count:                          # (each word read once: the drain must keep up, §7.1)
            word = pl.read_word(CONSOLE + (seen & (CONSOLE_BYTES - 4)))
            take = min(4 - (seen & 3), count - seen)
            console += (word >> (8 * (seen & 3))).to_bytes(4, "little")[:take]
            seen += take
        if status_word & 8:
            value = pl.read(REGS + 0x34)
            result = ("FAIL (partial tohost store)" if pl.read(REGS + 0x4C) != 0xF
                      else "PASS" if value == 1 else f"FAIL test={value >> 1}")
            break
        if entry["kind"] == "kernel" and status_word & 4:
            text = console.decode("ascii", "replace")
            lines = text.split("\n")
            complete = [line for line in lines[:-1] if line.startswith("A")]
            if complete:
                result = "PASS" if ",status=PASS," in complete[0] else "FAIL (kernel record)"
                break
        if pl.read64(REGS + 0x10) > entry["max_cycles"]:
            break
        if count == seen and pl.read(REGS + 0x08) == count:   # (no sleep while bytes flow)
            time.sleep(0.0005)
    report = {"name": entry["name"], "kind": entry["kind"], "status": result, "max_lag": lag,
              "retired1": pl.read64(REGS + 0x60), "load_seconds": loaded - load_started,
              "cycles": pl.read64(REGS + 0x10), "retired": pl.read64(REGS + 0x18),
              "window_cycles": pl.read64(REGS + 0x20), "window_retired": pl.read64(REGS + 0x28),
              "tohost": pl.read(REGS + 0x34), "tohost_cycles": pl.read64(REGS + 0x38),
              "tohost_retired": pl.read64(REGS + 0x50),
              "console": console.decode("ascii", "replace"), "seconds": time.monotonic() - started}
    report["console_bytes"] = pl.read(REGS + 0x08)
    pl.write(REGS + 0x00, 0)
    return report


def main():
    try:                                         # (20.5: the console's drain at real-time priority, so the reader
        os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))   # is not descheduled behind a burst)
    except (AttributeError, PermissionError, OSError):
        pass
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / "manifest.json").read_text())
    report = {"schema": "aster.18.7.board.v1", "status": "running", "board": os.uname().nodename,
              "kernel": os.uname().release, "programs": []}
    report_path = here / "report.json"
    global MAIN_BYTES, MAGIC, NPU_CONFIG, SOC_CONFIG, PAGE_BYTES, CONSOLE_BYTES
    if (here / "design.json").exists():
        design = json.loads((here / "design.json").read_text())
        MAIN_BYTES, MAGIC, NPU_CONFIG = design["main_bytes"], design["magic"], design.get("npu_config")
        SOC_CONFIG, PAGE_BYTES = design.get("soc_config"), design.get("page_bytes", PAGE_BYTES)
        CONSOLE_BYTES = design.get("console_bytes", CONSOLE_BYTES)
        report["design"] = design
    try:
        report["bitstream_sha256"] = hashlib.sha256((here / "aster_core.bit").read_bytes()).hexdigest()
        report["clock"] = set_fclk0(CLOCK_MHZ)  # before the load, so the design starts on its clock
        program_pl(here / "aster_core.bit")
        pl = Mmio(BASE, SIZE)
        identity = {"magic": pl.read(REGS + 0x40), "clk_hz": pl.read(REGS + 0x44), "main_bytes": pl.read(REGS + 0x48)}
        want = {"magic": MAGIC, "clk_hz": 100_000_000, "main_bytes": MAIN_BYTES}
        if NPU_CONFIG is not None:
            identity["npu_config"], want["npu_config"] = pl.read(REGS + 0x58), NPU_CONFIG
        if SOC_CONFIG is not None:
            identity["soc_config"], want["soc_config"] = pl.read(REGS + 0x5C), SOC_CONFIG
        report["identity"] = identity
        if identity != want:
            raise RuntimeError(f"wrong design identity {identity}")
        report["clock"]["measured_mhz"] = measure_clock(pl)
        for entry in manifest:
            result = run_program(pl, entry, (here / entry["image"]).read_bytes())
            report["programs"].append(result)
            print(f"{result['status']}: {entry['kind']} {entry['name']} cycles={result['cycles']} "
                  f"window_cycles={result['window_cycles']}", flush=True)
            report_path.write_text(json.dumps(report, indent=1))
        report["status"] = "complete"
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = repr(error)
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
