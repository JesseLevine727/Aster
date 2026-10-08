#!/usr/bin/env python3
"""Planted bugs in the Phase 20 DMA (milestone 20.3; docs/soc.md §10.5).

Each mutant is rtl/dma/aster_dma2.sv with one textual change: a bug the engine
could plausibly have. Each is built into the DMA shell (verification/dma) and
run in a battery of modes; the shell (its checks, or the engine's own
assertions) must report every one. A mutant the shell misses is a hole in the
shell, to be fixed, or an equivalent mutant, to be argued in the record.

    dma_mutants.py --build-dir build/dma/mutants [--jobs 8] [--only NAME]

First the unmutated engine runs the battery and must pass it. Prints one line a
mutant (CAUGHT with the shell's first report, or MISSED) and fails unless every
mutant is caught.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "rtl/dma/aster_dma2.sv"
# (mode, seed, jobs), at WAIT 0 and WAIT 3
BATTERY = [("sweep", 1, 0), ("ontime", 1, 150), ("stall", 1, 150), ("errors", 1, 40), ("abort", 1, 150),
           ("reset", 1, 150), ("stop", 1, 150), ("mix", 1, 150)]
WAITS = (0, 3)

MUTANTS: dict[str, list[tuple[str, str]]] = {
    # the read side
    "three_reads_in_flight": [("""- (ans_next ? 3'd1 : 3'd0) < 3'd2
                            && out_base + 3'd1 < 3'd4;""", """- (ans_next ? 3'd1 : 3'd0) < 3'd3
                            && out_base + 3'd1 < 3'd4;""")],
    "answer_credit_a_cycle_early": [("assign ans_next = acc_hist[HIST - 2];", "assign ans_next = acc_hist[HIST - 1];")],
    "read_buffer_one_too_many": [("&& out_base + 3'd1 < 3'd4;", "&& out_base + 3'd1 < 3'd5;")],
    "last_read_skipped": [("assign rd_more_taken = rd_left > UW'(1);", "assign rd_more_taken = rd_left > UW'(2);")],
    # the funnel
    "shift_off_by_one": [("desc_shift <= s > d ? 4'(s - d) : 4'(4'd8 + 4'(s) - 4'(d));",
                          "desc_shift <= s > d ? 4'(s - d + 1) : 4'(4'd8 + 4'(s) - 4'(d));")],
    "lag_on_equal_offsets": [("desc_lag <= s > d;", "desc_lag <= s >= d;")],
    "no_prime": [("assign prime = running && !abort_now && lag && !primed && rb_count != 0;",
                  "assign prime = 1'b0;")],
    "tail_takes_a_unit": [("assign take  = prime || (form && pop_left != 0);", "assign take  = prime || form;")],
    "first_lanes_off_by_one": [("desc_be_first <= 8'hFF << d;", "desc_be_first <= 8'hFF << (d + 3'd1);")],
    "last_lanes_one_more": [("desc_be_last  <= 8'hFF >> (3'd7 - last);", "desc_be_last  <= 8'hFF >> (3'd6 - last);")],
    "first_lanes_every_unit": [("assign unit_be = (first ? be_first : 8'hFF)", "assign unit_be = (be_first)")],
    "window_stale_source": [("assign window = {pop_left != 0 ? rbuf[rb_rd] : 64'b0, prev};",
                             "assign window = {prev, prev};")],
    # the descriptor's checks
    "checks_32_bit": [("s_end = {1'b0, source} + {1'b0, length};", "s_end = {1'b0, source + length};")],
    "limit_inclusive": [("if ({1'b0, source} < LIMIT_LO || s_end > LIMIT_HI) desc_error <= 2'd1;",
                         "if ({1'b0, source} < LIMIT_LO || s_end >= LIMIT_HI) desc_error <= 2'd1;")],
    "overlap_unchecked": [("else if ({1'b0, source} < d_end && {1'b0, destination} < s_end) desc_error <= 2'd3;",
                           "else if (1'b0) desc_error <= 2'd3;")],
    "length_zero_not_done": [("if (length == 0) done <= 1'b1;", "if (length == 0) running <= 1'b1;")],
    # the register port and its rules
    "hart1_not_ignored": [("assign own          = q_valid && q_write && !q_hart;", "assign own          = q_valid && q_write;")],
    "start_while_busy_accepted": [("|| (start_cmd && busy) || (ack_cmd && busy);", "|| (ack_cmd && busy);")],
    # completion and abort
    "done_before_last_answer": [("""assign terminal_success = running && !abort_now && wr_left == 0 && !w_req_valid && !w1_valid && inflight == 0
                              && (w_owed == 0 || (w_owed == 4'd1 && w_ans));""",
                                 """assign terminal_success = running && !abort_now && wr_left == 0 && !w_req_valid && !w1_valid && inflight == 0;""")],
    "abort_offers_buffered_write": [("w_req_valid <= w1_valid && !abort_now;", "w_req_valid <= w1_valid;")],
    "bytes_done_past_length": [("bytes_done <= upto > {1'b0, length} ? length : upto[31:0];", "bytes_done <= upto[31:0];")],
    "job_cycles_stopped": [("if (running) job_cycles <= job_cycles + 64'd1;", "if (running && !w_ans) job_cycles <= job_cycles + 64'd1;")],
    # the counters
    "payload_counts_writes": [("inc[4]  = run && w_acc ? 4'($countones(w_req_be)) : 4'd0;", "inc[4]  = run && w_acc ? 4'd8 : 4'd0;")],
    "invalidations_one_port": [("inc[9]  = 4'(ev_inval[0]) + 4'(ev_inval[1]);", "inc[9]  = 4'(ev_inval[0]);")],
    # from 20.3's first review (each missed by the shell then): the checks' boundaries, the cooperative
    # abort's no-withdraw rule, JOB_CYCLES and BYTES_DONE as they run, the ARM side, the counters' meanings
    "overlap_adjacent": [("else if ({1'b0, source} < d_end && {1'b0, destination} < s_end) desc_error <= 2'd3;",
                          "else if ({1'b0, source} <= d_end && {1'b0, destination} <= s_end) desc_error <= 2'd3;")],
    "lower_limit_exclusive": [("if ({1'b0, source} < LIMIT_LO || s_end > LIMIT_HI) desc_error <= 2'd1;",
                               "if ({1'b0, source} <= LIMIT_LO || s_end > LIMIT_HI) desc_error <= 2'd1;")],
    "dest_end_32_bit": [("d_end = {1'b0, destination} + {1'b0, length};", "d_end = {1'b0, destination + length};")],
    "abort_withdraws_write": [("end else if (abort_now) w1_valid <= 1'b0;",
                               "end else if (abort_now) begin w1_valid <= 1'b0; w_req_valid <= 1'b0; end")],
    "abort_withdraws_read": [("r_req_valid <= start_go || (r_acc ? valid_if_taken : (r_req_valid || valid_if_not));",
                              "r_req_valid <= start_go || (r_acc ? valid_if_taken : ((r_req_valid && !abort_now) || valid_if_not));")],
    "job_cycles_skip_stalls": [("if (running) job_cycles <= job_cycles + 64'd1;",
                                "if (running && !(w_req_valid && !w_req_ready)) job_cycles <= job_cycles + 64'd1;")],
    "zero_length_after_checks": [("if (length == 0) done <= 1'b1;\n                else if (desc_error != 0) begin done <= 1'b1; error <= 1'b1; error_code <= desc_error; end",
                                  "if (desc_error != 0) begin done <= 1'b1; error <= 1'b1; error_code <= desc_error; end\n                else if (length == 0) done <= 1'b1;")],
    "arm_lanes_swapped": [("w_req_be <= arm_addr[2] ? {arm_be, 4'h0} : {4'h0, arm_be};",
                           "w_req_be <= arm_addr[2] ? {4'h0, arm_be} : {arm_be, 4'h0};")],
    "busy_waits_one_port": [("inc[1]  = 4'(run && r_req_valid && !r_req_ready) + 4'(run && w_req_valid && !w_req_ready);",
                             "inc[1]  = 4'(run && ((r_req_valid && !r_req_ready) || (w_req_valid && !w_req_ready)));")],
    "bytes_done_on_accept": [("if (w_ans) begin\n                logic [32:0] upto;", "if (run && w_acc) begin\n                logic [32:0] upto;")],
    "abort_counts_as_success": [("inc[10] = 4'(terminal_success || (start_accept && length == 0));",
                                 "inc[10] = 4'(terminal_success || terminal_abort || (start_accept && length == 0));")],
    # 20.3's STOP and the counters' window
    "stop_keeps_requests": [("        end else if (clear) begin\n            r_req_valid <= 1'b0; w_req_valid <= 1'b0; w1_valid <= 1'b0;\n        end else if (!run) begin",
                             "        end else if (!run) begin")],
    "counts_on_freeze": [("else if (win_add) count[i] <= count[i] + 64'(inc_q2[i]);",
                          "else if (win_counting) count[i] <= count[i] + 64'(inc_q2[i]);")],
    "window_a_cycle_early": [("else if (win_add) count[i] <= count[i] + 64'(inc_q2[i]);",
                              "else if (win_add) count[i] <= count[i] + 64'(inc_q[i]);")],
}


def build(name: str, edits: list[tuple[str, str]], out: Path, wait: int) -> Path:
    text = SOURCE.read_text()
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"{name}: the edit's anchor appears {text.count(old)} times")
        text = text.replace(old, new)
    work = (out / f"{name}_w{wait}").resolve()
    work.mkdir(parents=True, exist_ok=True)
    (work / "aster_dma2.sv").write_text(text)
    sim = work / "dma_shell"
    result = subprocess.run(["verilator", "--cc", "--exe", "--build", "-O3", "--assert", "-Wno-fatal", "--public-flat-rw",
                             "--top-module", "aster_dma2", f"-GWAIT={wait}", "-CFLAGS",
                             f"-O2 -DWAIT_CYCLES={wait} -I{ROOT / 'verification/dma'}", "--Mdir", str(work / "obj"),
                             "-o", str(sim), str(work / "aster_dma2.sv"), str(ROOT / "verification/dma/tb_dma.cpp")],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{name}: the build failed\n{result.stderr[-2000:]}")
    return sim


def battery(sim: Path) -> str | None:
    """The first report of the battery (None: every run passed)."""
    for mode, seed, jobs in BATTERY:
        args = [str(sim), f"+seed={seed}", f"+mode={mode}"] + ([f"+jobs={jobs}"] if jobs else [])
        result = subprocess.run(args, capture_output=True, text=True, timeout=1800)
        last = (result.stdout.strip().splitlines() or [""])[-1]
        if result.returncode != 0 or not last.startswith("PASS"):
            report = next((l for l in (result.stdout + result.stderr).splitlines() if "Assertion failed" in l), "")
            return f"{mode}: " + (report.split(": ", 2)[-1] if report else last.split(" — ")[-1][:160])
    return None


def try_mutant(name: str, out: Path) -> str:
    for wait in WAITS:
        report = battery(build(name, MUTANTS.get(name, []), out, wait))
        if report:
            return f"CAUGHT: {name} (WAIT {wait}, {report})"
    return f"MISSED: {name}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--only")
    args = parser.parse_args()
    for wait in WAITS:
        report = battery(build("unmutated", [], args.build_dir, wait))
        if report:
            print(f"FAIL: the unmutated engine fails the battery at WAIT {wait}: {report}")
            return 1
    names = [n for n in MUTANTS if not args.only or n == args.only]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda n: try_mutant(n, args.build_dir), names))
    for line in results:
        print(line)
    caught = sum(line.startswith("CAUGHT") for line in results)
    print(f"{'PASS' if caught == len(names) else 'FAIL'}: planted bugs in aster_dma2: {caught} of {len(names)} caught "
          f"by the DMA shell")
    return 0 if caught == len(names) else 1


if __name__ == "__main__":
    sys.exit(main())
