#!/usr/bin/env python3
"""Planted bugs in a Phase 20 fabric (milestone 20.0 on, docs/soc.md §10.1).

Each mutant is the DUT's source with one textual change: a bug a fabric could
plausibly have. Each is built into the fabric shell (verification/fabric) and
run in a battery of modes and seeds; the shell must report every one. A
mutant the shell misses is either a hole in the shell (to be fixed) or an
equivalent mutant (to be argued in the record). In 20.0 the DUT is the serial
reference fabric; the banked fabric (20.1) gets its own list.

    fabric_mutants.py --build-dir build/fabric/mutants [--jobs 8] [--only NAME]

First the unmutated DUT runs the battery and must pass it. Then a mutant is
caught only by a rule's report (not COVERAGE or BANKS_MISMATCH, which a shift
in stimulus could cause). Prints one line a mutant (CAUGHT with the shell's
first report, or MISSED) and fails unless every mutant is caught.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DUT = "verification/fabric/ref_fabric.sv"
BANKS = 0
OVERTAKE = 16
BATTERY = [("mix", 1), ("hot", 1), ("hot", 2), ("reset", 1), ("solo", 1), ("solo1", 1), ("errors", 1), ("dense", 1)]
CYCLES = 100000

HOLD = "assign hold = {(amo1 && amo1_hart) || (amo2 && amo2_hart), (amo1 && !amo1_hart) || (amo2 && !amo2_hart)};"
KEEP = "if (hart_exception[h] || !hart_rst_n[h]) keep = 1'b0;"
WRITE_END = "if (wrote && writer != (h == 0 ? D0 : D1) && touches(waddr[31:3], wbe, word)) keep = 1'b0;"
READ_RULE = "else if (!(wr_granted && write_unit == unit[k]) && !(amo2 && amo_unit == unit[k]))"
SNOOP_PORT = "automatic int port = writer == N ? 1 : writer == W ? 2 : 0;"
MUTANTS: dict[str, list[tuple[str, str]]] = {
    "no_amo_hold": [(HOLD, "assign hold = 2'b00;")],
    "amo_hold_one_cycle": [(HOLD, "assign hold = {amo1 && amo1_hart, amo1 && !amo1_hart};")],
    "unit_rule_off": [("if (grant[r] && tgt_mem[r] && !is_write[r] && unit[r] == unit[k]) conflict = 1'b1;", ""),
                      (READ_RULE, "else if (!(amo2 && amo_unit == unit[k]))")],
    "unit_rule_read_side": [(READ_RULE, "else if (!(amo2 && amo_unit == unit[k]))")],
    "amo_write_unit_readable": [(READ_RULE, "else if (!(wr_granted && write_unit == unit[k]))")],
    "write_beside_amo_write": [("conflict = wr_granted || amo1 || amo2;", "conflict = wr_granted || amo1;")],
    "amo_beside_amo_write": [("conflict = wr_granted || amo1 || amo2;",
                              "conflict = wr_granted || amo1 || (amo2 && !((k == D0 || k == D1) && d_req_op[k == D0 ? 0 : 1] > OP_SC));")],
    "snoop_late": [("    logic [1:0]       resv;\n",
                    "    logic [1:0]       resv;\n    logic [1:0][2:0] snoop_d; logic [1:0][2:0][27:0] snoop_ld;\n"),
                   ("            snoop_valid <= '0;\n            if (wrote)",
                    "            snoop_d <= '0; snoop_valid <= snoop_d; snoop_line <= snoop_ld;\n            if (wrote)"),
                   ("snoop_valid[c][port] <= 1'b1;", "snoop_d[c][port] <= 1'b1;"),
                   ("snoop_line[c][port]  <= waddr[31:4];", "snoop_ld[c][port] <= waddr[31:4];")],
    "snoop_wrong_port": [(SNOOP_PORT, "automatic int port = writer == N ? 0 : writer == W ? 2 : 1;")],
    "snoop_to_writer": [("if (writer != (c == 0 ? D0 : D1)) begin", "if (1'b1) begin")],
    "snoop_wrong_line": [("snoop_line[c][port]  <= waddr[31:4];", "snoop_line[c][port]  <= waddr[31:4] ^ 28'd1;")],
    "own_store_ends_reservation": [(WRITE_END, "if (wrote && touches(waddr[31:3], wbe, word)) keep = 1'b0;")],
    "own_amo_ends_reservation": [(WRITE_END, "if (wrote && (writer != (h == 0 ? D0 : D1) || amo2) && touches(waddr[31:3], wbe, word)) keep = 1'b0;")],
    "write_ends_old_word": [(WRITE_END, "if (wrote && writer != (h == 0 ? D0 : D1) && touches(waddr[31:3], wbe, resv_word[h])) keep = 1'b0;")],
    "reservation_by_unit": [("return (wunit == word[29:1]) && (word[0] ? |be8[7:4] : |be8[3:0]);", "return (wunit == word[29:1]);")],
    "sc_by_unit": [("sc_ok = resv[h] && resv_word[h] == addr[k][31:2];", "sc_ok = resv[h] && resv_word[h][29:1] == addr[k][31:3];")],
    "exception_keeps_reservation": [(KEEP, "if (!hart_rst_n[h]) keep = 1'b0;")],
    "reset_keeps_reservation": [(KEEP, "if (hart_exception[h]) keep = 1'b0;")],
    "lr_beats_exception": [(KEEP, "if ((hart_exception[h] || !hart_rst_n[h]) && !lr_now) keep = 1'b0;")],
    "reset_keeps_answers": [("                    pipe_v[h == 0 ? I0 : I1] <= '0; pipe_v[h == 0 ? D0 : D1] <= '0;\n", "")],
    "held_hart_accepted": [("valid    = {w_req_valid, r_req_valid, n_req_valid, d_req_valid[1] && hart_rst_n[1],\n"
                            "                    i_req_valid[1] && hart_rst_n[1],",
                            "valid    = {w_req_valid, r_req_valid, n_req_valid, d_req_valid[1],\n"
                            "                    i_req_valid[1],")],
    "fixed_priority": [("prio <= prio == 3'(NREQ - 1) ? 3'd0 : prio + 3'd1;", "prio <= '0;")],
    "io_answer_early": [("pipe_d[k][0]  <= '0;", "pipe_d[k][0]  <= 64'(io_rsp_rdata);"),
                        ("pipe_io[k][0] <= grant[k] && tgt_io[k];", "pipe_io[k][0] <= 1'b0;")],
    "io_wrong_hart": [("io_req_hart = 1'(h);", "io_req_hart = 1'(1 - h);")],
    "two_io_a_cycle": [("if (!io_taken) begin grant[k] = 1'b1; io_taken = 1'b1; end", "begin grant[k] = 1'b1; io_taken = 1'b1; end")],
    "error_late": [("err_q[k]      <= grant[k] && tgt_err[k];", "err_q[k]      <= pipe_v[k][0] && err_q[k];")],
    "error_has_effect": [("            tgt_err[k] = !tgt_mem[k] && !tgt_io[k];", "            tgt_err[k] = !tgt_mem[k] && !tgt_io[k] && k != W;"),
                         ("            tgt_mem[k] = in_mem(addr[k]);", "            tgt_mem[k] = in_mem(addr[k]) || (k == W && !in_mem(addr[k]));")],
    "lr_no_reservation": [("automatic logic        lr_now = mine && d_req_op[h] == OP_LR;", "automatic logic        lr_now = 1'b0;")],
    "sc_keeps_reservation": [("if (mine && d_req_op[h] == OP_SC) keep = 1'b0;", "")],
    "amo_wrong_op": [("4'd5: return 5'b00000; 4'd6: return 5'b00100;", "4'd5: return 5'b00100; 4'd6: return 5'b00000;")],
    "solo_i_behind_d_read": [(READ_RULE, READ_RULE[:-1] + " && !(k == I0 && grant[D0] && tgt_mem[D0] && unit[D0] == unit[k]))")],
}


NOT_A_CATCH = ("COVERAGE", "BANKS_MISMATCH")


def build(name: str, edits: list[tuple[str, str]], out: Path) -> Path:
    source = (ROOT / DUT).read_text()
    for old, new in edits:
        if source.count(old) != 1:
            raise SystemExit(f"FAIL: mutant {name}: its pattern matches {source.count(old)} times: {old[:70]!r}")
        source = source.replace(old, new)
    work = out / name
    work.mkdir(parents=True, exist_ok=True)
    (work / Path(DUT).name).write_text(source)
    sim = (work / "sim").resolve()
    result = subprocess.run(["verilator", "--cc", "--exe", "--build", "-O3", "--assert", "-Wno-fatal", "--top-module",
                             "shell_fabric", "--prefix", "Vfabric_shell", "--Mdir", str(work / "obj"), "-o", str(sim),
                             f"-DFABRIC_DUT={Path(DUT).stem}", str(ROOT / "rtl/aster_core/aster_core_pkg.sv"),
                             str(work / Path(DUT).name), str(ROOT / "verification/fabric/shell_fabric.sv"),
                             str(ROOT / "verification/fabric/tb_fabric.cpp"),
                             "-CFLAGS", f"-std=c++20 -I{ROOT / 'verification/fabric'}"], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"FAIL: building mutant {name}: {result.stderr[-1500:]}")
    return sim


def try_mutant(name: str, out: Path) -> str:
    sim = build(name, MUTANTS.get(name, []), out)
    for mode, seed in BATTERY:
        result = subprocess.run([str(sim), f"+banks={BANKS}", f"+overtake_limit={OVERTAKE}", f"+seed={seed}",
                                 f"+cycles={CYCLES}", f"+mode={mode}", "+require_coverage"],
                                capture_output=True, text=True, timeout=1800)
        line = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
        if result.returncode:
            fields = line.split()
            status = fields[1] if len(fields) > 1 else line
            detail = line.split(" at=", 1)[1] if " at=" in line else ""
            if status in NOT_A_CATCH:
                return f"MISSED: {name} ({mode}, seed {seed}): only {status} {detail}"[:220]
            return f"CAUGHT: {name} ({mode}, seed {seed}): {status} {detail}"[:220]
    return f"MISSED: {name} (every mode of the battery passed)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/fabric/mutants")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--only")
    args = parser.parse_args()
    names = [args.only] if args.only else list(MUTANTS)
    clean = try_mutant("unmutated", args.build_dir)
    if not clean.startswith("MISSED: unmutated (every mode"):
        print(f"FAIL: the unmutated {Path(DUT).stem} does not pass the battery: {clean}")
        return 1
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        lines = list(pool.map(lambda n: try_mutant(n, args.build_dir), names))
    print("\n".join(lines))
    missed = [line for line in lines if line.startswith("MISSED")]
    print(f"{'PASS' if not missed else 'FAIL'}: planted bugs in {Path(DUT).stem}: {len(lines) - len(missed)} of "
          f"{len(lines)} caught by the fabric shell")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
