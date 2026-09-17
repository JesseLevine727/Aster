#!/usr/bin/env python3
"""Read-only interface-freeze guard for Aster v1.0.

Extracts the frozen address decodes, ABI constants and instruction encoding from
the RTL and compares them against the v1.0 contract. Any drift fails, so a
post-freeze change to a register map, ABI or instruction encoding is caught.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (label, file, regex, expected)
CHECKS = [
    ("uart page", "rtl/soc/aster_coherent_soc.sv",
     r"wire uart_access\s*=[^;]*20'h([0-9a-fA-F]+)", "20000"),
    ("timer page", "rtl/soc/aster_coherent_soc.sv",
     r"wire timer_access\s*=[^;]*20'h([0-9a-fA-F]+)", "20001"),
    ("hart control page", "rtl/soc/aster_coherent_soc.sv",
     r"wire control_access\s*=[^;]*20'h([0-9a-fA-F]+)", "20002"),
    ("interrupt controller page", "rtl/soc/aster_coherent_soc.sv",
     r"wire irq_access\s*=[^;]*20'h([0-9a-fA-F]+)", "20004"),
    ("dma page", "rtl/soc/aster_coherent_soc.sv",
     r"wire dma_access\s*=[^;]*20'h([0-9a-fA-F]+)", "30000"),
    ("npu page", "rtl/soc/aster_coherent_soc.sv",
     r"wire npu_access\s*=[^;]*20'h([0-9a-fA-F]+)", "40000"),
    ("perf hart 0 bank", "rtl/soc/aster_coherent_soc.sv",
     r"m_addr\[31:8\]\s*==\s*24'h([0-9a-fA-F]+)\)\s*m_rdata\s*=\s*perf_rdata\[0\]", "200030"),
    ("perf hart 1 bank", "rtl/soc/aster_coherent_soc.sv",
     r"m_addr\[31:8\]\s*==\s*24'h([0-9a-fA-F]+)\)\s*m_rdata\s*=\s*perf_rdata\[1\]", "200031"),
    ("dot8 perf bank", "rtl/soc/aster_coherent_soc.sv",
     r"m_addr\[31:8\]\s*==\s*24'h([0-9a-fA-F]+)\)\s*m_rdata\s*=\s*dot8_rdata", "200032"),
    ("coherent perf base", "rtl/soc/aster_coherent_soc.sv",
     r"BASE_ADDR\(32'h([0-9a-fA-F_]+)\s*\+\s*h\*256\)", "2000_3000"),
    ("coherent perf ABI", "rtl/peripherals/aster_coherent_perf.sv",
     r"32'h84:\s*rdata\s*=\s*(\d+)", "4"),
    ("coherent perf command", "rtl/soc/aster_coherent_soc.sv",
     r"m_addr\s*==\s*32'h([0-9a-fA-F_]+)\s*&&\s*m_mask\[0\]", "2000_3080"),
    ("legacy perf ABI", "rtl/peripherals/aster_perf_counters.sv",
     r"parameter int unsigned ABI_VERSION\s*=\s*(\d+)", "2"),
    ("timer ABI", "rtl/peripherals/aster_timer.sv",
     r"parameter int unsigned ABI_VERSION\s*=\s*(\d+)", "1"),
    ("interrupt ABI", "rtl/peripherals/aster_interrupt_controller.sv",
     r"parameter int unsigned ABI_VERSION\s*=\s*(\d+)", "1"),
    ("interrupt source count", "rtl/peripherals/aster_interrupt_controller.sv",
     r"parameter int unsigned SOURCE_COUNT\s*=\s*(\d+)", "4"),
    ("dma engine ABI", "rtl/dma/aster_dma_engine.sv",
     r"12'h01c:\s*cfg_rdata\s*=\s*(\d+)", "1"),
    ("dma perf ABI", "rtl/peripherals/aster_dma_perf.sv",
     r"12'h184:\s*rdata\s*=\s*(\d+)", "5"),
    ("dot8 perf ABI", "rtl/peripherals/aster_dot8_perf.sv",
     r"12'h284:\s*rdata\s*=\s*(\d+)", "6"),
    ("bridge version", "rtl/soc/aster_pynq_linux.sv",
     r"ENABLE_NPU\s*\?\s*32'h([0-9a-fA-F_]+)", "00090001"),
    ("dot8 decode mask", "rtl/core/aster_pcpi_dot8.sv",
     r"\(pcpi_insn & 32'h([0-9a-fA-F_]+)\)\s*==\s*32'h([0-9a-fA-F_]+)", "fe00_707f:0000_000b"),
    ("dot8 software encoding", "software/drivers/aster_dot8.h",
     r"\.insn r (0x[0-9a-fA-F]+), (\d+), (\d+)", "0x0b:0:0"),
]

# Frozen permitted MMIO pages in the atomic fabric.
FABRIC_PAGES = {"20000", "20001", "20002", "20003", "20004", "30000", "40000"}


def read(relative):
    return (ROOT / relative).read_text()


def check_regex(label, relative, pattern, expected):
    text = read(relative)
    match = re.search(pattern, text)
    if not match:
        return f"{label}: pattern not found in {relative}"
    if ":" in expected and expected.count(":") >= 1 and match.lastindex and match.lastindex > 1:
        actual = ":".join(group.lower().replace("_", "") for group in match.groups())
        wanted = ":".join(part.lower().replace("_", "") for part in expected.split(":"))
    else:
        actual = match.group(1).lower().replace("_", "")
        wanted = expected.lower().replace("_", "")
    if actual != wanted:
        return f"{label}: {actual} != frozen {wanted}"
    return None


def check_fabric_pages():
    text = read("rtl/interconnect/aster_atomic_fabric.sv")
    found = {value.lower() for value in re.findall(r"address\[31:12\]\s*==\s*20'h([0-9a-fA-F]+)", text)}
    if found != FABRIC_PAGES:
        return f"atomic fabric permitted pages {sorted(found)} != frozen {sorted(FABRIC_PAGES)}"
    return None


def audit():
    failures = [error for error in
                (check_regex(*check) for check in CHECKS) if error]
    fabric = check_fabric_pages()
    if fabric:
        failures.append(fabric)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    failures = audit()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    if not args.quiet:
        for label, *_ in CHECKS:
            print(f"OK: {label}")
        print(f"OK: atomic fabric permitted pages {sorted(FABRIC_PAGES)}")
    print(f"PASS: {len(CHECKS) + 1} frozen v1.0 interfaces match the RTL")


if __name__ == "__main__":
    main()
