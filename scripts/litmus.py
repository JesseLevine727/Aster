#!/usr/bin/env python3
"""Classify Phase 20's litmus counts (milestone 20.0; docs/soc.md §10.3).

software/tests/litmus_v2.c runs each shape LITMUS_TRIALS times on two harts and
counts every outcome's key in litmus_counts[shape][key], which Spike's
+signature (and the simulations) dump one word a line. This script names each
outcome, fails the run on any outcome RVWMO forbids (or any key the shape
cannot produce; key 15 is a read outside the shape's possible values) and on
a shape whose trials do not add up, and prints the counts of the allowed
outcomes. LRSC_PEER's forbidden outcome, an sc failing with no write to its
word, is this design's contract (soc.md §4.5): RVWMO itself allows spurious
sc failures.

The keys (litmus_v2.c's key()): x and y start at 0; "x" and "y" below are their
final values, r0/r1 what hart 0 and hart 1 read.

    litmus.py --signature spike.sig --trials 200 [--platform spike|rtl]
    litmus.py --selftest

--platform spike allows sc failures in LRSC_PEER: Spike ends a reservation at
its instruction-step boundaries, which a hardware fabric must not (cpu.md §6).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

KEYS = 16


def coherence_pairs() -> dict[int, str]:
    names = {}
    for a in range(3):
        for b in range(3):
            names[a | b << 2] = f"r1 reads x={a} then x={b}"
    return names


# name: (key names: allowed outcomes, forbidden key names). Keys not listed are impossible.
SHAPES: list[tuple[str, dict[int, str], dict[int, str]]] = [
    ("MP", {0: "r1: y=0 x=0", 1: "r1: y=1 x=0 (reordered; RVWMO allows without fences)", 2: "r1: y=0 x=1",
            3: "r1: y=1 x=1"}, {}),
    ("MP_F", {0: "r1: y=0 x=0", 2: "r1: y=0 x=1", 3: "r1: y=1 x=1"}, {1: "r1: y=1 x=0 across fences"}),
    ("SB", {0: "r0=0 r1=0 (store buffering; RVWMO allows without fences)", 1: "r0=1 r1=0", 2: "r0=0 r1=1",
            3: "r0=1 r1=1"}, {}),
    ("SB_F", {1: "r0=1 r1=0", 2: "r0=0 r1=1", 3: "r0=1 r1=1"}, {0: "r0=0 r1=0 across fence rw,rw"}),
    ("LB", {0: "r0=0 r1=0", 1: "r0=1 r1=0", 2: "r0=0 r1=1", 3: "r0=1 r1=1 (load buffering; RVWMO allows)"}, {}),
    ("LB_F", {0: "r0=0 r1=0", 1: "r0=1 r1=0", 2: "r0=0 r1=1"}, {3: "r0=1 r1=1 across fence rw,rw"}),
    ("S_F", {0: "r1: y=0, x=1", 1: "r1: y=1, x=1", 2: "r1: y=0, x=2"}, {3: "r1: y=1 but x=2"}),
    ("R_F", {0: "r1: x=0, y=1", 1: "r1: x=1, y=1", 3: "r1: x=1, y=2"}, {2: "r1: x=0 but y=2"}),
    ("W22_F", {1: "x=2 y=1", 2: "x=1 y=2", 3: "x=2 y=2"}, {0: "x=1 y=1"}),
    ("CORR", {0: "r1 reads x=0 then 0", 2: "r1 reads x=0 then 1", 3: "r1 reads x=1 then 1"},
     {1: "r1 reads x=1 then 0"}),
    ("COWR", {1: "r0=1, x=1", 5: "r0=1, x=2", 6: "r0=2, x=2"},
     {0: "r0=0 after its own store", 4: "r0=0 after its own store", 2: "r0=2 but x=1"}),
    ("CORW", {0: "r0=0, x=1", 4: "r0=0, x=2", 2: "r0=2, x=1"},
     {1: "r0 read its own later store", 5: "r0 read its own later store", 6: "r0=2 but x=2"}),
    ("COWW", {k: v for k, v in coherence_pairs().items() if (k & 3) <= (k >> 2)},
     {**{k: v + " (backwards)" for k, v in coherence_pairs().items() if (k & 3) > (k >> 2)},
      15: "x is not 2, or a read outside 0-2"}),
    ("LRSC", {0: "every lr/sc increment present"}, {1: "an increment lost"}),
    ("LRSC_PEER", {0: "no sc failure, every increment present"},
     {1: "an sc failed with no write to its word", 2: "an increment lost", 3: "both"}),
    ("AMO", {0: "every amoadd present"}, {1: "an amoadd lost"}),
    ("PUB", {0: "the payload seen whole after the flag"}, {1: "a payload word stale after the flag"}),
    ("S", {0: "r1: y=0, x=1", 1: "r1: y=1, x=1", 2: "r1: y=0, x=2", 3: "r1: y=1, x=2 (RVWMO allows without fences)"}, {}),
    ("R", {0: "r1: x=0, y=1", 1: "r1: x=1, y=1", 2: "r1: x=0, y=2 (RVWMO allows without fences)", 3: "r1: x=1, y=2"}, {}),
    ("W22", {0: "x=1 y=1 (RVWMO allows without fences)", 1: "x=2 y=1", 2: "x=1 y=2", 3: "x=2 y=2"}, {}),
    ("SB_SC", {1: "r0=1 r1=0", 2: "r0=0 r1=1", 3: "r0=1 r1=1"}, {0: "r0=0 r1=0 under seq_cst"}),
    ("LB_SC", {0: "r0=0 r1=0", 1: "r0=1 r1=0", 2: "r0=0 r1=1"}, {3: "r0=1 r1=1 under seq_cst"}),
    ("SB_SC_SAME", {1: "r0=1 r1=0", 2: "r0=0 r1=1", 3: "r0=1 r1=1"}, {0: "r0=0 r1=0 under seq_cst, one unit"}),
    ("LB_SC_SAME", {0: "r0=0 r1=0", 1: "r0=1 r1=0", 2: "r0=0 r1=1"}, {3: "r0=1 r1=1 under seq_cst, one unit"}),
    ("MP_F_SAME", {0: "r1: y=0 x=0", 2: "r1: y=0 x=1", 3: "r1: y=1 x=1"}, {1: "r1: y=1 x=0 across fences, one unit"}),
    ("SB_F_SAME", {1: "r0=1 r1=0", 2: "r0=0 r1=1", 3: "r0=1 r1=1"}, {0: "r0=0 r1=0 across fence rw,rw, one unit"}),
]


def classify(words: list[int], trials: int, platform: str) -> tuple[bool, list[str]]:
    lines, ok = [], True
    if len(words) != len(SHAPES) * KEYS:
        return False, [f"FAIL: litmus: {len(words)} signature words, expected {len(SHAPES) * KEYS}"]
    for index, (name, allowed, forbidden) in enumerate(SHAPES):
        counts = words[index * KEYS:(index + 1) * KEYS]
        allowed = dict(allowed)
        if platform == "spike" and name == "LRSC_PEER":
            allowed[1] = "an sc failed at a Spike step boundary (allowed on Spike only)"
        bad = []
        for key, count in enumerate(counts):
            if not count or key in allowed:
                continue
            bad.append(f"{count} x key {key}: {forbidden.get(key, 'an impossible outcome')}")
        total = sum(counts)
        if total != trials:
            bad.append(f"{total} trials counted, expected {trials}")
        seen = ", ".join(f"{allowed[k]}: {c}" for k, c in enumerate(counts) if c and k in allowed)
        if bad:
            ok = False
            lines.append(f"FAIL: litmus {name}: " + "; ".join(bad) + (f" (allowed seen: {seen})" if seen else ""))
        else:
            lines.append(f"PASS: litmus {name}, {trials} trials, nothing forbidden: {seen}")
    return ok, lines


def selftest() -> int:
    """Every forbidden and impossible key, and a wrong total, must be rejected; a clean run accepted."""
    trials, failures, planted = 10, [], 0
    clean = []
    for name, allowed, _ in SHAPES:
        counts = [0] * KEYS
        counts[min(allowed)] = trials
        clean += counts
    ok, _ = classify(clean, trials, "rtl")
    if not ok:
        failures.append("a clean run was rejected")
    for index, (name, allowed, _) in enumerate(SHAPES):
        for key in range(KEYS):
            if key in allowed:
                continue
            words = list(clean)
            words[index * KEYS + min(allowed)] -= 1
            words[index * KEYS + key] += 1
            planted += 1
            if classify(words, trials, "rtl")[0]:
                failures.append(f"{name} key {key} accepted")
        words = list(clean)
        words[index * KEYS + min(allowed)] -= 1
        planted += 1
        if classify(words, trials, "rtl")[0]:
            failures.append(f"{name} with a trial missing accepted")
    if failures:
        print("FAIL: litmus classifier self-tests: " + "; ".join(failures))
        return 1
    print(f"PASS: the litmus classifier's self-tests: {planted} planted forbidden, impossible or missing outcomes "
          f"rejected, a clean run accepted")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--trials", type=int)
    parser.add_argument("--platform", choices=("spike", "rtl"), default="rtl")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.signature or not args.trials:
        parser.error("--signature and --trials are required")
    words = [int(line, 16) for line in args.signature.read_text().split()]
    ok, lines = classify(words, args.trials, args.platform)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
