#!/usr/bin/env python3
"""The 18.7 performance gate (docs/cpu.md §7): the Aster core against PicoRV32
on the CPU set, from the two kernel runs' logs.

For each kernel the speedup is PicoRV32's window cycles divided by the Aster
core's over the same measurement window — both runs must have retired the
same number of instructions in it (the same work; the runs also match the
retained Phase 17 baseline's window and checksum). The gate passes only if
the geometric mean of the seven gate kernels' speedups is at least 2.0x and
none is below 1.5x, on the unrounded ratios. Every speedup is printed, and
the minimal top's Conv2D (a cross-check outside the gate) too.

The measurement conditions are §7's: PicoRV32 in its look-ahead shell (make
core-kernels, its log build/core_tests/kernels.log), the Aster core in the
two-port shell on the one-cycle memory without its L1 (make
core-aster-kernels, build/core_tests/aster-kernels.log).

    performance_gate.py PICORV32_LOG ASTER_LOG
"""
from __future__ import annotations

import math
from pathlib import Path
import re
import sys

GATE = ("coremark", "dhrystone", "sort_search", "fft", "strided", "conv2d_scalar_coh", "reduction")
GEOMEAN_MIN, KERNEL_MIN = 2.0, 1.5


def windows(log: Path) -> dict[str, tuple[int, int]]:
    """Each passing kernel's (window cycles, window instructions)."""
    found = {}
    for line in log.read_text().splitlines():
        match = re.match(r"PASS: kernel (\S+) PASS .*?window_cycles=(\d+) window_retired=(\d+)", line)
        if match:
            found[match.group(1)] = (int(match.group(2)), int(match.group(3)))
    return found


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    pico, aster = windows(Path(sys.argv[1])), windows(Path(sys.argv[2]))
    missing = [name for name in GATE if name not in pico or name not in aster]
    if missing:
        print(f"FAIL: no passing run of {', '.join(missing)} in both logs")
        return 1
    print(f"  {'kernel':20s} {'instructions':>12s} {'PicoRV32 cycles':>16s} {'Aster cycles':>13s} {'speedup':>9s}")
    speedups, mismatched = {}, []
    for name in [*GATE, *sorted(set(pico) & set(aster) - set(GATE))]:
        (p_cycles, p_retired), (a_cycles, a_retired) = pico[name], aster[name]
        if p_retired != a_retired:
            mismatched.append(name)
        speedup = p_cycles / a_cycles
        if name in GATE:
            speedups[name] = speedup
        print(f"  {name:20s} {a_retired:>12d} {p_cycles:>16d} {a_cycles:>13d} {speedup:>8.3f}x"
              + ("" if name in GATE else "  (not in the gate)"))
    if mismatched:
        print(f"FAIL: the two runs did different work in {', '.join(mismatched)}")
        return 1
    geomean = math.prod(speedups.values()) ** (1 / len(speedups))
    lowest = min(speedups, key=speedups.get)
    passed = geomean >= GEOMEAN_MIN and speedups[lowest] >= KERNEL_MIN
    print(f"{'PASS' if passed else 'FAIL'}: the §7 performance gate — geometric mean {geomean:.3f}x "
          f"(at least {GEOMEAN_MIN}x), lowest {lowest} {speedups[lowest]:.3f}x (at least {KERNEL_MIN}x), "
          f"over the {len(GATE)} gate kernels")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
