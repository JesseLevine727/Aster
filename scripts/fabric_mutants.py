#!/usr/bin/env python3
"""Planted bugs in a Phase 20 fabric (milestone 20.0 on, docs/soc.md §10.1).

Each mutant is the DUT's source with one textual change: a bug a fabric could
plausibly have. Each is built into the fabric shell (verification/fabric) and
run in a battery of modes and seeds; the shell must report every one. A
mutant the shell misses is either a hole in the shell (to be fixed) or an
equivalent mutant (to be argued in the record). --dut chooses the fabric:
ref_fabric, the serial reference (20.0), or aster_fabric, the banked fabric
(20.1), each with its own list.

    fabric_mutants.py --dut aster_fabric --build-dir build/fabric/mutants [--jobs 8] [--only NAME]

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
# (mode, seed, WAIT): a build at each WAIT the battery uses
BATTERY = [("mix", 1, 0), ("hot", 1, 0), ("hot", 2, 0), ("reset", 1, 0), ("solo", 1, 0), ("solo1", 1, 0), ("errors", 1, 0),
           ("dense", 1, 0), ("hammer", 1, 0), ("twin", 1, 0), ("edges", 1, 0), ("reset", 2, 2)]
CYCLES = 100000

HOLD = "assign hold = {(amo1 && amo1_hart) || (amo2 && amo2_hart), (amo1 && !amo1_hart) || (amo2 && !amo2_hart)};"
KEEP = "if (hart_exception[h] || !hart_rst_n[h]) keep = 1'b0;"
WRITE_END = "if (wrote && writer != (h == 0 ? D0 : D1) && touches(waddr[31:3], wbe, word)) keep = 1'b0;"
READ_RULE = "else if (!(wr_granted && write_unit == unit[k]) && !(amo2 && amo_unit == unit[k]))"
SNOOP_PORT = "automatic int port = writer == N ? 1 : writer == W ? 2 : 0;"
REF_MUTANTS: dict[str, list[tuple[str, str]]] = {
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


# The banked fabric (rtl/fabric/aster_fabric.sv).
B_ELIG_A = "&& !((k == D0 && hold[0]) || (k == D1 && hold[1])) && !(wclass[k] && amo_busy[b])"
B_KEEP = "if (hart_exception[h] || !hart_rst_n[h]) resv[h] <= 1'b0;"
BANKED_MUTANTS: dict[str, list[tuple[str, str]]] = {
    "no_amo_hold": [("hold = amo1 | amo2;", "hold = '0;")],
    "amo_hold_one_cycle": [("hold = amo1 | amo2;", "hold = amo1;")],
    "io_ignores_amo_hold": [("if (valid[D0] && tgt_io[D0] && !hold[0] && (!io_ptr || !(valid[D1] && tgt_io[D1] && !hold[1]))) io_grant[D0] = 1'b1;",
                             "if (valid[D0] && tgt_io[D0] && (!io_ptr || !(valid[D1] && tgt_io[D1]))) io_grant[D0] = 1'b1;")],
    "port_b_read_beside_write": [("take_b[b][j] = pick_b[b][j] && !blocked;", "take_b[b][j] = pick_b[b][j];")],
    "writes_beside_amo": [(B_ELIG_A, "&& !((k == D0 && hold[0]) || (k == D1 && hold[1]))")],
    "port_a_beside_amo_write": [("elig_a[b][i] = valid[k] && on_port_a[k] && tgt_mem[k] && bank[k] == 2'(b) && !amo_wr_now[b]",
                                 "elig_a[b][i] = valid[k] && on_port_a[k] && tgt_mem[k] && bank[k] == 2'(b)")],
    "amo_unit_readable": [("                               && !(amo_wr_now[b] && index[k] == amo_index[b]);", "                               ;")],
    "fixed_priority": [("if (|pick_a[b]) ptr_a[b] <= after(pick_a[b]);", ";"),
                       ("if (|take_b[b]) ptr_b[b] <= after(take_b[b]);", ";")],
    # (port B's pointer moving past a pick port A held back: equivalent at the contract — the held-back
    # requester loses its turn, but no wait exceeds the fairness bound the shell checks)
    "npu_write_on_port_b": [("on_port_a[D0] = 1'b1; on_port_a[D1] = 1'b1; on_port_a[W] = 1'b1; on_port_a[N] = n_req_we;",
                             "on_port_a[D0] = 1'b1; on_port_a[D1] = 1'b1; on_port_a[W] = 1'b1; on_port_a[N] = 1'b0;")],
    "answer_wrong_port": [("unit_data = port2[k] ? q_b[bank2[k]] : q_a[bank2[k]];", "unit_data = port2[k] ? q_a[bank2[k]] : q_b[bank2[k]];")],
    "answer_wrong_half": [("{32'b0, half2[k] ? unit_data[63:32] : unit_data[31:0]}", "{32'b0, half2[k] ? unit_data[31:0] : unit_data[63:32]}")],
    "bank_index_aliased": [("index[k]   = IB'({unit[k][UB-1:3], unit[k][0]});", "index[k]   = IB'(unit[k]);")],
    "amo_writes_wrong_half": [("we_a[b]   = amo_hi[b] ? 8'hF0 : 8'h0F;", "we_a[b]   = amo_hi[b] ? 8'h0F : 8'hF0;")],
    "amo_old_wrong_half": [("amo_new[b] = {aster_core_pkg::amo_value(amo_f5[b], q_a[b][63:32], amo_operand_b[b]),",
                            "amo_new[b] = {aster_core_pkg::amo_value(amo_f5[b], q_a[b][31:0], amo_operand_b[b]),")],
    "amo_wrong_op": [("amo_f5[b]        <= funct5_of(amo1_op[h]);", "amo_f5[b]        <= 5'b00000;")],
    "byte_enables_ignored": [("we_a[b]   = eff_write[ga[i]] ? be8[ga[i]] : 8'h00;", "we_a[b]   = eff_write[ga[i]] ? 8'hFF : 8'h00;")],
    "sc_without_reservation": [("sc_ok[h == 0 ? D0 : D1] = resv[h] && resv_word[h] == d_req_addr[h][AB-1:2];",
                                "sc_ok[h == 0 ? D0 : D1] = resv_word[h] == d_req_addr[h][AB-1:2];")],
    "sc_by_unit": [("sc_ok[h == 0 ? D0 : D1] = resv[h] && resv_word[h] == d_req_addr[h][AB-1:2];",
                    "sc_ok[h == 0 ? D0 : D1] = resv[h] && resv_word[h][AB-3:1] == d_req_addr[h][AB-1:3];")],
    # (20.3: the reserved word kept as its offset in main memory; its top bit compared, 64 KiB apart)
    "sc_ignores_top_offset_bit": [("sc_ok[h == 0 ? D0 : D1] = resv[h] && resv_word[h] == d_req_addr[h][AB-1:2];",
                                   "sc_ok[h == 0 ? D0 : D1] = resv[h] && resv_word[h][AB-4:0] == d_req_addr[h][AB-2:2];")],
    "sc_keeps_reservation": [("end else if (sc_now || touched) resv[h] <= 1'b0;", "end else if (touched) resv[h] <= 1'b0;")],
    "writes_never_end_reservations": [("end else if (sc_now || touched) resv[h] <= 1'b0;", "end else if (sc_now) resv[h] <= 1'b0;")],
    "own_writes_end_reservation": [("for (int w = 0; w < 4; w++) if (w != h && wrote[w] && touches_resv[h][w]) touched = 1'b1;",
                                    "for (int w = 0; w < 4; w++) if (wrote[w] && touches_resv[h][w]) touched = 1'b1;")],
    "exception_keeps_reservation": [(B_KEEP, "if (!hart_rst_n[h]) resv[h] <= 1'b0;")],
    "reservation_event_lost": [("ev_resv_end[h] <= resv[h] && !lr_now && !sc_now && touched;", "ev_resv_end[h] <= 1'b0;")],
    "snoop_to_writer": [("snoop_valid[c] <= {wrote[3], wrote[2], wrote[1 - c]};", "snoop_valid[c] <= {wrote[3], wrote[2], wrote[c]};"),
                        ("snoop_line[c]  <= {wr_addr[3][31:4], wr_addr[2][31:4], wr_addr[1 - c][31:4]};",
                         "snoop_line[c]  <= {wr_addr[3][31:4], wr_addr[2][31:4], wr_addr[c][31:4]};")],
    "snoop_wrong_port": [("snoop_valid[c] <= {wrote[3], wrote[2], wrote[1 - c]};", "snoop_valid[c] <= {wrote[2], wrote[3], wrote[1 - c]};")],
    "snoop_wrong_line": [("snoop_line[c]  <= {wr_addr[3][31:4], wr_addr[2][31:4], wr_addr[1 - c][31:4]};",
                          "snoop_line[c]  <= {wr_addr[3][31:4], wr_addr[2][31:4] ^ 28'd4, wr_addr[1 - c][31:4]};")],
    "held_hart_accepted": [("n_req_valid, d_req_valid[1] && hart_rst_n[1],", "n_req_valid, d_req_valid[1],")],
    "reset_keeps_answers": [("v2[h == 0 ? I0 : I1] <= 1'b0; v2[h == 0 ? D0 : D1] <= 1'b0;", ";")],
    "error_has_effect": [("tgt_err[k] = !tgt_mem[k] && !tgt_io[k];", "tgt_err[k] = !tgt_mem[k] && !tgt_io[k] && k != W;"),
                         ("tgt_mem[k] = main_hint[k];", "tgt_mem[k] = main_hint[k] || k == W;")],
    # (20.2: the fabric takes each requester's main-memory flag instead of decoding the range; an NPU
    # request outside main memory taken as memory's replaces the range planted too short)
    "npu_error_has_effect": [("tgt_mem[k] = main_hint[k];", "tgt_mem[k] = main_hint[k] || k == N;")],
    "two_io_a_cycle": [("else if (valid[D1] && tgt_io[D1] && !hold[1]) io_grant[D1] = 1'b1;",
                        "if (valid[D1] && tgt_io[D1] && !hold[1]) io_grant[D1] = 1'b1;")],
    # fairness (the reviews of 20.1)
    "sticky_winner": [("if (|pick_a[b]) ptr_a[b] <= after(pick_a[b]);", "if (|pick_a[b]) ptr_a[b] <= after(pick_a[b]) - 2'd1;")],
    "port_b_fixed_priority": [("if (|take_b[b]) ptr_b[b] <= after(take_b[b]);", ";")],
    "io_fixed_priority": [("if (io_grant[D0]) io_ptr <= 1'b1;", ";")],
    "starvation_unmasked": [("&& !(blk_v[b] && wclass[k] && index[k] == blk_index[b]);", ";")],
    "wait_stages_kept_on_reset": [("for (int s = 0; s < WAIT; s++) begin wv[s][h == 0 ? I0 : I1] <= 1'b0; wv[s][h == 0 ? D0 : D1] <= 1'b0; end", ";")],
    # (20.1's second chance on port B, and its six mutants, were removed with it in 20.2)
}

DUTS = {
    "ref_fabric": {"files": ["verification/fabric/ref_fabric.sv"], "mutated": "verification/fabric/ref_fabric.sv",
                   "banks": 0, "overtake": 16, "mutants": REF_MUTANTS},
    "aster_fabric": {"files": ["rtl/fabric/aster_fabric_bank.sv", "rtl/fabric/aster_fabric.sv"],
                     "mutated": "rtl/fabric/aster_fabric.sv", "banks": 4, "overtake": 12, "mutants": BANKED_MUTANTS},
}


def build(dut: dict, name: str, edits: list[tuple[str, str]], out: Path, wait: int = 0) -> Path:
    source = (ROOT / dut["mutated"]).read_text()
    for old, new in edits:
        if source.count(old) != 1:
            raise SystemExit(f"FAIL: mutant {name}: its pattern matches {source.count(old)} times: {old[:70]!r}")
        source = source.replace(old, new)
    work = out / name / f"w{wait}"
    work.mkdir(parents=True, exist_ok=True)
    (work / Path(dut["mutated"]).name).write_text(source)
    sources = [str(work / Path(f).name) if f == dut["mutated"] else str(ROOT / f) for f in dut["files"]]
    sim = (work / "sim").resolve()
    result = subprocess.run(["verilator", "--cc", "--exe", "--build", "-O3", "--assert", "-Wno-fatal", "--top-module",
                             "shell_fabric", "--prefix", "Vfabric_shell", "--Mdir", str(work / "obj"), "-o", str(sim),
                             f"-DFABRIC_DUT={Path(dut['mutated']).stem}", f"-GWAIT={wait}", str(ROOT / "rtl/aster_core/aster_core_pkg.sv"),
                             *sources, str(ROOT / "verification/fabric/shell_fabric.sv"),
                             str(ROOT / "verification/fabric/tb_fabric.cpp"),
                             "-CFLAGS", f"-std=c++20 -I{ROOT / 'verification/fabric'}"], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"FAIL: building mutant {name}: {result.stderr[-1500:]}")
    return sim


def try_mutant(dut: dict, name: str, out: Path) -> str:
    sims = {}
    for mode, seed, wait in BATTERY:
        if wait not in sims:
            sims[wait] = build(dut, name, dut["mutants"].get(name, []), out, wait)
        sim = sims[wait]
        result = subprocess.run([str(sim), f"+banks={dut['banks']}", f"+overtake_limit={dut['overtake']}", f"+seed={seed}",
                                 f"+cycles={CYCLES}", f"+mode={mode}", "+require_coverage"],
                                capture_output=True, text=True, timeout=1800)
        line = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
        if result.returncode:
            fields = line.split()
            status = fields[1] if len(fields) > 1 else line
            detail = line.split(" at=", 1)[1] if " at=" in line else ""
            if status in NOT_A_CATCH:
                return f"MISSED: {name} ({mode}, seed {seed}, WAIT {wait}): only {status} {detail}"[:220]
            return f"CAUGHT: {name} ({mode}, seed {seed}, WAIT {wait}): {status} {detail}"[:220]
    return f"MISSED: {name} (every mode of the battery passed)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dut", choices=sorted(DUTS), default="aster_fabric")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/fabric/mutants")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--only")
    args = parser.parse_args()
    dut = DUTS[args.dut]
    out = args.build_dir / args.dut
    names = [args.only] if args.only else list(dut["mutants"])
    clean = try_mutant(dut, "unmutated", out)
    if not clean.startswith("MISSED: unmutated (every mode"):
        print(f"FAIL: the unmutated {args.dut} does not pass the battery: {clean}")
        return 1
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        lines = list(pool.map(lambda n: try_mutant(dut, n, out), names))
    print("\n".join(lines))
    missed = [line for line in lines if line.startswith("MISSED")]
    print(f"{'PASS' if not missed else 'FAIL'}: planted bugs in {args.dut}: {len(lines) - len(missed)} of "
          f"{len(lines)} caught by the fabric shell")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
