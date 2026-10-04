"""Planted bugs in the Aster core's RTL (docs/phase18.md: "Planted bugs" in
milestones 18.3-18.6 and the ACT4 adoption).

Each mutant replaces one exact piece of rtl/aster_core/aster_core{,_pkg,_fetch}.sv
or, from 18.6, of the L1 caches aster_l1{i,d}.sv (its anchor, which must occur
exactly once — verification/host/test_mutation_campaign.py checks that against
the current RTL) and is built into the two-port shell with assertions off: the
core's mutants without the caches, the caches' mutants (and the core's that only
the caches can expose, CACHED) with them and the cache reference model
(the aster_l1 DUT). It must then be caught: the verification runs fail-fast from
the cheapest stage (the suites with the CPI check — without it on the cached
core, whose misses the CPI model does not know — back-pressure, long stalls on
the cached core, random programs, random interrupts, arch-test, and ACT4; then,
for a cache's mutant, that cache's random unit test, make core-aster-l1-unit's
seeds) and the first failing stage is reported; MISSED means none failed. A few are
recorded as equivalent or unobservable (docs/phase18.md, 18.3).

    python3 scripts/mutation_campaign.py OUTDIR [NAME ...]   # all mutants, or those named
    ONLY_ACT4=1 python3 scripts/mutation_campaign.py OUTDIR  # each mutant against ACT4 alone

Needs the tools of make core-aster-tests (Spike, its Xasterdot8 extension) and,
for the ACT4 stage, make core-aster-act4's ELFs under build/act4. Eight mutants
run at a time; each one's directory under OUTDIR is deleted once its verdict is
reached (a mutant's builds and logs reach a few hundred MB). Each verdict is one
line, "<name>: CAUGHT by <stage>: …", "<name>: MISSED" or "<name>: BUILD FAILED …".
"""
from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "rtl/aster_core"
SPIKE = os.environ.get("SPIKE", os.path.expanduser("~/tools/spike/bin/spike"))
C, P, F = "", "_pkg", "_fetch"     # aster_core.sv, aster_core_pkg.sv, aster_core_fetch.sv
I, D = "l1i", "l1d"                # aster_l1i.sv, aster_l1d.sv (18.6)
FILES = {C: "aster_core.sv", P: "aster_core_pkg.sv", F: "aster_core_fetch.sv", I: "aster_l1i.sv", D: "aster_l1d.sv"}
L1_RAM = RTL / "aster_l1_ram.sv"
MUTANTS = {
 # trap entry
 "mepc-exception-next-pc": (C, "mepc         <= m1_trap ? m1.pc[31:2] : m1.next_pc[31:2];", "mepc         <= m1.next_pc[31:2];"),
 "mepc-interrupt-own-pc": (C, "mepc         <= m1_trap ? m1.pc[31:2] : m1.next_pc[31:2];", "mepc         <= m1.pc[31:2];"),
 "mpie-not-saved": (C, "                mstatus_mpie <= mstatus_mie;\n", "                mstatus_mpie <= 1'b1;\n"),
 "mie-not-cleared-on-trap": (C, "                mstatus_mpie <= mstatus_mie;\n                mstatus_mie  <= 1'b0;", "                mstatus_mpie <= mstatus_mie;\n                mstatus_mie  <= mstatus_mie;"),
 "mcause-interrupt-bit-missing": (C, "{1'b1, 27'b0, irq_code}", "{1'b0, 27'b0, irq_code}"),
 "mtval-interrupt-garbage": (C, "mtval        <= m1_trap ? trap_tval : 32'b0;", "mtval        <= trap_tval;"),
 "tval-illegal-zero": (C, "4'd2:                   trap_tval = m1.insn;", "4'd2:                   trap_tval = 32'b0;"),
 "tval-target-is-pc": (C, "4'd0:                   trap_tval = m1.next_pc;", "4'd0:                   trap_tval = m1.pc;"),
 "tval-access-is-pc": (C, "4'd4, 4'd5, 4'd6, 4'd7: trap_tval = m1.addr;", "4'd4, 4'd5, 4'd6, 4'd7: trap_tval = m1.pc;"),
 "bus-error-cause-swapped": (C, "m1.store ? 4'd7 : 4'd5;", "m1.store ? 4'd5 : 4'd7;"),
 "ecall-ebreak-codes-swapped": (C, "e_dec.ebreak ? 4'd3 : e_dec.ecall ? 4'd11", "e_dec.ebreak ? 4'd11 : e_dec.ecall ? 4'd3"),
 "trap-record-next-pc": (C, "                        m2.next_pc <= {mtvec, 2'b00};\n", ""),
 "16bit-insn-full-word": (C, "e_insn  <= {d_insn[1:0] == 2'b11 ? d_insn[31:16] : 16'b0, d_insn[15:0]};", "e_insn  <= d_insn;"),
 # redirect and kill
 "trap-does-not-redirect": (C, "assign f_redirect = e_flush || kill;", "assign f_redirect = e_flush;"),
 "interrupt-target-wrong": (C, "assign f_target   = m1_stop ? mtvec : e_redirect_target[31:2];", "assign f_target   = m1_trap ? mtvec : e_redirect_target[31:2];"),
 "kill-without-advance": (C, "assign kill       = m1_stop && m1_advance;", "assign kill       = m1_stop;"),
 "decode-survives-kill": (C, "            // Decode (a squashed instruction leaves: Decode is free)\n            if (kill) begin", "            // Decode (a squashed instruction leaves: Decode is free)\n            if (1'b0) begin"),
 "fetch-loses-trap-while-presented": (F, "            e_pending <= e_flush;", "            e_pending <= e_flush && !e_pending;"),
 "mret-no-redirect": (C, "assign e_mispredict = e_dec.jalr || e_dec.mret ||", "assign e_mispredict = e_dec.jalr ||"),
 "mret-wrong-target": (C, "assign e_redirect_target = e_dec.mret ? {mepc, 2'b00} :", "assign e_redirect_target = e_dec.mret ? e_link :"),
 "mret-mpie-not-set": (C, "assign mret_status = {19'b0, 2'b11, 3'b0, 1'b1, 3'b0, mstatus_mpie, 3'b0};", "assign mret_status = {19'b0, 2'b11, 3'b0, mstatus_mpie, 3'b0, mstatus_mpie, 3'b0};"),
 # interrupts
 "irq-ignores-mie-bit": (C, "assign irq_pending = mstatus_mie && ", "assign irq_pending = 1'b1 && "),
 "irq-priority-msi-first": (C, "assign irq_code    = mip_q[2] && mie_meie ? 4'd11 : mip_q[0] && mie_msie ? 4'd3 : 4'd7;", "assign irq_code    = mip_q[0] && mie_msie ? 4'd3 : mip_q[2] && mie_meie ? 4'd11 : 4'd7;"),
 "irq-after-csr": (C, "assign m1_irq      = m1.valid && !m1.sys && irq_pending;", "assign m1_irq      = m1.valid && irq_pending;"),
 "irq-lines-swapped": (C, "mip_q    <= {meip, mtip, msip};", "mip_q    <= {msip, mtip, meip};"),
 "request-not-gated-by-irq": (C, "assign d_req_valid = e_live && e_access && e_ready && m1_free && !m1_stop;", "assign d_req_valid = e_live && e_access && e_ready && m1_free && !m1_trap;"),
 "execute-not-gated-by-irq": (C, "assign e_advance = e_live && e_ready && m1_free && !m1_stop &&", "assign e_advance = e_live && e_ready && m1_free && !m1_trap &&"),
 "exception-loses-to-irq": (C, "mcause       <= m1_trap ? {28'b0, trap_cause} : {1'b1, 27'b0, irq_code};", "mcause       <= m1_irq ? {1'b1, 27'b0, irq_code} : {28'b0, trap_cause};"),
 "no-intr-mark": (C, "            if (kill) intr_next <= 1'b1;", "            if (kill) intr_next <= 1'b0;"),
 # CSRs
 "no-serialization": (C, " && (!e_dec.sys || (!m1.valid && !m2.valid))\n", "\n"),
 "serialization-m1-only": (C, "(!e_dec.sys || (!m1.valid && !m2.valid))", "(!e_dec.sys || !m1.valid)"),
 "csr-write-on-trap": (C, "e_slot.csr_we    = (e_dec.csr_write || e_dec.mret) && !e_trap;", "e_slot.csr_we    = (e_dec.csr_write || e_dec.mret);"),
 "read-only-csr-writable": (P, "d.illegal   = d.csr_sel == '0 || (d.csr_write && insn[31:30] == 2'b11);", "d.illegal   = d.csr_sel == '0;"),
 "csr-read-always-writes": (P, "d.csr_write = funct3[1:0] == 2'd1 || insn[19:15] != 5'd0;", "d.csr_write = 1'b1;"),
 "csr-set-clear-swapped": (C, ": e_dec.funct3[1:0] == 2'd2 ? csr_rdata | csr_src : csr_rdata & ~csr_src;", ": e_dec.funct3[1:0] == 2'd2 ? csr_rdata & ~csr_src : csr_rdata | csr_src;"),
 "csr-imm-uses-register": (C, "assign csr_src   = e_dec.uses_rs1 ? rs1f : e_dec.imm;", "assign csr_src   = rs1f;"),
 "mstatus-mpie-from-bit3": (C, "                    mstatus_mpie <= m1.wdata[7];", "                    mstatus_mpie <= m1.wdata[3];"),
 "mie-mtie-from-bit3": (C, "                    mie_mtie <= m1.wdata[7];", "                    mie_mtie <= m1.wdata[3];"),
 "mepc-read-bit0": (C, "| ({32{e_dec.csr_sel.mepc}}          & {mepc, 2'b00})", "| ({32{e_dec.csr_sel.mepc}}          & {mepc, 2'b01})"),
 # counters
 "minstret-counts-traps": (C, "m1.sys ? m1_first : m1_advance && !m1_trap);", "m1.sys ? m1_first : m1_advance);"),
 "csr-write-every-m1-cycle": (C, "assign m1_csr_we = m1.valid && m1.csr_we && m1_first;", "assign m1_csr_we = m1.valid && m1.csr_we;"),
 "sys-retire-at-commit": (C, "assign retire    = m1.valid && (m1.sys ? m1_first : m1_advance && !m1_trap);", "assign retire    = m1.valid && m1_advance && !m1_trap;"),
 "trap-rvfi-tval-not-carried": (C, "                        m2.tval    <= trap_tval;", "                        m2.tval    <= 32'b0;"),
 "trap-rvfi-mpie-not-carried": (C, "                        m2.mpie    <= mstatus_mie;", "                        m2.mpie    <= 1'b1;"),
 "mip-layout-swapped": (C, "assign mip_value           = {20'b0, mip_q[2], 3'b0, mip_q[1], 3'b0, mip_q[0], 3'b0};", "assign mip_value           = {20'b0, mip_q[0], 3'b0, mip_q[1], 3'b0, mip_q[2], 3'b0};"),
 "minstret-write-loses-to-increment": (C, "            if (m1_csr_we && m1.csr_sel.minstret)       minstret[31:0]  <= m1.wdata;\n            else if (m1_csr_we && m1.csr_sel.minstreth) minstret[63:32] <= m1.wdata;\n            else if (retire && !ir_inhibit)             minstret        <= minstret + 64'd1;",
                                     "            if (m1_csr_we && m1.csr_sel.minstret)       minstret[31:0]  <= m1.wdata;\n            else if (m1_csr_we && m1.csr_sel.minstreth) minstret[63:32] <= m1.wdata;\n            if (retire && !ir_inhibit && !m1_first)     minstret        <= minstret + 64'd1;"),
 "time-stuck": (C, "            mtime_q  <= mtime;\n", ""),
 "timeh-reads-low": (C, "| ({32{e_dec.csr_sel.time_hi}}       & mtime_q[63:32])", "| ({32{e_dec.csr_sel.time_hi}}       & mtime_q[31:0])"),
 "time-obeys-cy": (C, "            mtime_q  <= mtime;\n", "            if (!cy_inhibit) mtime_q <= mtime;\n"),
 "time-reads-mcycle": (C, "| ({32{e_dec.csr_sel.time_lo}}       & mtime_q[31:0])", "| ({32{e_dec.csr_sel.time_lo}}       & mcycle[31:0])"),
 "fencei-no-redirect": (C, "assign e_mispredict = e_dec.jalr || e_dec.mret || e_dec.fencei", "assign e_mispredict = e_dec.jalr || e_dec.mret"),
 "fencei-target-wrong": (C, ": (e_pred || e_dec.fencei) ? e_link : e_btarget;", ": e_pred ? e_link : e_btarget;"),
 "fencei-no-drain": (C, "\n                      && (!e_dec.fencei || (!m1.valid && !m2.valid));", ";"),
 "fencei-drain-m1-only": (C, "(!e_dec.fencei || (!m1.valid && !m2.valid))", "(!e_dec.fencei || !m1.valid)"),
 "fencei-illegal": (P, "d.illegal = funct3 > 3'd1;", "d.illegal = funct3 != 3'd0;"),
 "amo-min-is-max": (P, "5'b10000: d.amo_op = OP_MIN;", "5'b10000: d.amo_op = OP_MAX;"),
 "lr-rs2-legal": (P, "d.illegal = insn[24:20] != 5'd0;", "d.illegal = 1'b0;"),
 "lr-is-store": (P, "d.amo_op = OP_LR; d.store = 1'b0;", "d.amo_op = OP_LR;"),
 "atomic-not-load": (P, "d.atomic = 1'b1; d.load = 1'b1; d.store = 1'b1;", "d.atomic = 1'b1; d.load = 1'b0; d.store = 1'b1;"),
 "sc-failure-reports-store": (C, "rvfi_mem_wmask <= w.store && (!w_sc || w.rdata == 32'b0) ? w.be : 4'b0;", "rvfi_mem_wmask <= w.store ? w.be : 4'b0;"),
 "amo-rvfi-add-wrong": (P, "5'b00000: return old + operand;", "5'b00000: return old - operand;"),
 "misa-no-a": (C, "localparam logic [31:0] MISA = 32'h4080_1101;", "localparam logic [31:0] MISA = 32'h4080_1100;"),
 "dot8-not-late-in-m1": (C, "e_slot.late      = (e_dec.load || e_dec.mul || e_dec.dot8) && !e_trap;", "e_slot.late      = (e_dec.load || e_dec.mul) && !e_trap;"),
 "dot8-no-decode-interlock": (C, "return e_dec.load || e_dec.mul || e_dec.dot8;", "return e_dec.load || e_dec.mul;"),
 "dot8-interlock-in-m1": (C, "m1.late && !m1.dot8;", "m1.late;"),
 "dot8-late-in-m2": (C, "m2.late      <= m1.late && !m1.dot8 && !m1_bus_err;", "m2.late      <= m1.late && !m1_bus_err;"),
 "dot8-result-not-to-m2": (C, "if (m1.dot8) m2.result <= 32'($signed(dot8_sum));", ""),
 "dot8-lane0-unsigned": (C, "assign dot8_p0  = $signed(m1.rs1v[7:0])   * $signed(m1.rs2v[7:0]);", "assign dot8_p0  = 16'(m1.rs1v[7:0] * m1.rs2v[7:0]);"),
 "dot8-sum-17-bits": (C, "if (m1.dot8) m2.result <= 32'($signed(dot8_sum));", "if (m1.dot8) m2.result <= 32'($signed(dot8_sum[16:0]));"),
 "dot8-lane-swap": (C, "assign dot8_p0  = $signed(m1.rs1v[7:0])   * $signed(m1.rs2v[7:0]);", "assign dot8_p0  = $signed(m1.rs1v[7:0])   * $signed(m1.rs2v[31:24]);"),
 "dot8-ignores-funct7": (P, "5'b00010: if (funct3 == 3'd0 && funct7 == 7'h00) begin", "5'b00010: if (funct3 == 3'd0) begin"),
 "dot8-ignores-funct3": (P, "5'b00010: if (funct3 == 3'd0 && funct7 == 7'h00) begin", "5'b00010: if (funct7 == 7'h00) begin"),
 "dot8-sum-zero-extended": (C, "if (m1.dot8) m2.result <= 32'($signed(dot8_sum));", "if (m1.dot8) m2.result <= {14'b0, dot8_sum};"),
 "dot8-lane3-dropped": (C, "assign dot8_sum = 18'(dot8_p0) + 18'(dot8_p1) + 18'(dot8_p2) + 18'(dot8_p3);", "assign dot8_sum = 18'(dot8_p0) + 18'(dot8_p1) + 18'(dot8_p2);"),
 "misa-no-x": (C, "localparam logic [31:0] MISA = 32'h4080_1101;", "localparam logic [31:0] MISA = 32'h4000_1101;"),
 "hpm-events-illegal": (P, "(address >= 12'h323 && address <= 12'h33F)) c.zero = 1'b1;", "1'b0) c.zero = 1'b1;"),
 "minstret-ignores-inhibit": (C, "else if (retire && !ir_inhibit)             minstret        <= minstret + 64'd1;", "else if (retire)                            minstret        <= minstret + 64'd1;"),
 "minstret-write-not-suppressing": (C, "            if (m1_csr_we && m1.csr_sel.minstret)       minstret[31:0]  <= m1.wdata;\n            else if (m1_csr_we && m1.csr_sel.minstreth) minstret[63:32] <= m1.wdata;\n            else if (retire && !ir_inhibit)             minstret        <= minstret + 64'd1;",
                                    "            if (retire && !ir_inhibit)                  minstret        <= minstret + 64'd1;\n            if (m1_csr_we && m1.csr_sel.minstret)       minstret[31:0]  <= m1.wdata;\n            else if (m1_csr_we && m1.csr_sel.minstreth) minstret[63:32] <= m1.wdata;"),
 # 18.6: the core's side of the caches
 "fencei-no-invalidate": (C, "fencei_inval <= e_advance && e_dec.fencei;", "fencei_inval <= 1'b0;"),
 # 18.6: the instruction cache
 "l1i-hit-ignores-valid": (I, "assign lookup_hit   = valid[s1_addr[11:4]] && tag_ram", "assign lookup_hit   = tag_ram"),
 "l1i-tag-drops-bit12": (I, "tag_ram[s1_addr[11:4]] == s1_addr[31:12];", "tag_ram[s1_addr[11:4]][19:1] == s1_addr[31:13];"),
 "l1i-fencei-ignored": (I, "            if (invalidate) valid <= '0;", "            if (1'b0) valid <= '0;"),
 "l1i-refill-not-poisoned": (I, "received == 3'd3 && !poisoned) valid", "received == 3'd3) valid"),
 "l1i-waiting-fetch-not-stale": (I, "                if (refill_write) s1_stale <= 1'b1;\n", ""),
 "l1i-miss-answers-word0": (I, "if (received[1:0] == s2_addr[3:2]) s2_word <= m_rsp_data;", "if (received[1:0] == 2'd0) s2_word <= m_rsp_data;"),
 "l1i-kept-word-ignored": (I, "assign i_rsp_data   = s2_have || state == ANSWER ? s2_word : rd_data;", "assign i_rsp_data   = state == ANSWER ? s2_word : rd_data;"),
 "l1i-refill-writes-issued-slot": (I, ".wr_addr({s2_addr[11:4], received[1:0]}),", ".wr_addr({s2_addr[11:4], issued[1:0]}),"),
 # 18.6: the data cache
 "l1d-store-hit-not-written": (D, "&& s2_cacheable && s2_hit;", "&& s2_cacheable && s2_hit && 1'b0;"),
 "l1d-store-miss-written": (D, "&& s2_cacheable && s2_hit;", "&& s2_cacheable;"),
 "l1d-store-all-bytes": (D, ".wr_be(refill_write ? 4'hf : store_write ? s2_be : 4'h0),", ".wr_be(refill_write ? 4'hf : store_write ? 4'hf : 4'h0),"),
 "l1d-amo-keeps-line": (D, "if (amo_inval) valid_next[s2_addr[11:4]] = 1'b0;", "if (1'b0) valid_next[s2_addr[11:4]] = 1'b0;"),
 "l1d-lr-invalidates": (D, "s2_op != OP_STORE && s2_op != OP_LR && s2_op != OP_LOAD;", "s2_op != OP_STORE && s2_op != OP_LOAD;"),
 "l1d-io-load-refills": (D, "end else if (s1_cacheable && s1_load) begin", "end else if (s1_load) begin"),
 "l1d-error-a-cycle-late": (D, "assign d_rsp_error  = s1_valid && s1_age == 2'd0 && s1_error;", "assign d_rsp_error  = s1_valid && s1_age == 2'd1 && s1_error;"),
 "l1d-now-ignores-stale": (D, "assign s1_now       = s1_age == 2'd0 && !s1_stale && !array_write;", "assign s1_now       = s1_age == 2'd0 && !array_write;"),
 "l1d-waiting-load-not-stale": (D, "                if (array_write) s1_stale <= 1'b1;\n", ""),
 "l1d-accepted-during-write-not-stale": (D, "                s1_stale <= array_write;", "                s1_stale <= 1'b0;"),
 "l1d-miss-answers-word0": (D, "if (received[1:0] == s2_addr[3:2]) s2_word <= m_rsp_rdata;", "if (received[1:0] == 2'd0) s2_word <= m_rsp_rdata;"),
 "l1d-refill-writes-issued-slot": (D, ".wr_addr(refill_write ? {s2_addr[11:4], received[1:0]} : s2_addr[11:2]),", ".wr_addr(refill_write ? {s2_addr[11:4], issued[1:0]} : s2_addr[11:2]),"),
 "l1d-kept-word-ignored": (D, "assign d_rsp_rdata  = s2_have || state == ANSWER ? s2_word : rd_data;", "assign d_rsp_rdata  = state == ANSWER ? s2_word : rd_data;"),
 "l1d-everything-posted": (D, "s2_post      <= s1_cacheable && s1_op == OP_STORE;", "s2_post      <= 1'b1;"),
 "l1d-io-store-posted": (D, "s2_post      <= s1_cacheable && s1_op == OP_STORE;", "s2_post      <= s1_op == OP_STORE;"),
 "l1d-posted-answers-not-dropped": (D, "posted   <= posted + (m_accept && state == ACCESS && s2_post ? 2'd1 : 2'd0) - (m_drop ? 2'd1 : 2'd0);", "posted   <= '0;"),
 "l1d-inflight-unlimited": (D, "assign m_req_valid  = inflight != 2'd2 && (", "assign m_req_valid  = ("),
 "l1d-snoop-not-invalidating": (D, "assign snoop_hit = snoop_valid &&", "assign snoop_hit = 1'b0 && snoop_valid &&"),
 "l1d-snoop-no-bypass": (D, "                          && !snoop_s1;", "                          ;"),
 "l1d-refill-not-poisoned-by-snoop": (D, "if (snoop_s2) poisoned <= 1'b1;", ""),
 "l1d-refill-keeps-replaced-line": (D, "if (refill_start) valid_next[s1_addr[11:4]] = 1'b0;", ""),
 "l1d-snooped-last-word-installed": (D, "&& !poisoned && !snoop_s2;", "&& !poisoned;"),
 "l1i-no-hold-after-fencei": (I, "&& (!fenced || m_waiting);", ";"),
 "l1i-hold-withdraws-waiting": (I, "(!fenced || m_waiting)", "!fenced"),
}
# The core's mutants that only the caches' timing exposes: run on the cached core.
CACHED = {"fencei-no-invalidate"}


def cached(name: str) -> bool:
    return MUTANTS[name][0] in (I, D) or name in CACHED


def sources() -> dict[str, str]:
    return {k: (RTL / name).read_text() for k, name in FILES.items()}


def stages(l1: bool) -> list[tuple[str, list[str]]]:
    act4 = ("act4", ["--act4", str(ROOT / "build/act4/aster-rv32ima/elfs")])
    if os.environ.get("ONLY_ACT4") == "1":
        return [act4]
    if l1:
        return [("suites", []),
                ("suites stall", ["--stall-seed", "5"]),
                ("suites long stall", ["--stall-seed", "3", "--shell-arg", "+long_stall"]),
                ("random", ["--random", "10"]),
                ("random long stall", ["--random", "10", "--random-seed", "701", "--stall-seed", "17",
                                       "--shell-arg", "+long_stall"]),
                ("irq splice", ["--interrupts", "7"]),
                ("random irq stall", ["--random", "5", "--random-seed", "401", "--interrupts", "7", "--stall-seed", "13"]),
                ("arch", ["--arch"]),
                ("suites latency1 stall", ["--stall-seed", "9", "--shell-arg", "+latency=1"]),
                ("suites inflight4", ["--stall-seed", "7", "--shell-arg", "+max_inflight=4"]),
                act4,
                ("act4 stall", ["--act4", str(ROOT / "build/act4/aster-rv32ima/elfs"), "--stall-seed", "5"])]
    return [("suites cpi", ["--cpi-check"]),
            ("suites stall", ["--stall-seed", "5"]),
            ("random cpi", ["--random", "10", "--cpi-check"]),
            ("irq splice", ["--interrupts", "7"]),
            ("random irq", ["--random", "5", "--random-seed", "301", "--interrupts", "7"]),
            ("random irq stall", ["--random", "5", "--random-seed", "401", "--interrupts", "7", "--stall-seed", "13"]),
            ("arch cpi", ["--arch", "--cpi-check"]),
            ("suites latency1 stall", ["--stall-seed", "9", "--shell-arg", "+latency=1"]),
            ("random stall", ["--random", "10", "--random-seed", "101", "--stall-seed", "11"]),
            ("suites inflight4", ["--stall-seed", "7", "--shell-arg", "+max_inflight=4"]),
            act4]


UNIT_SEEDS, UNIT_CYCLES = 200, 1_000_000     # make core-aster-l1-unit's


def unit_build(which: str, work: Path) -> list[str]:
    """The Verilator command for a cache's unit test (verification/core/l1) on the mutant's RTL."""
    tests = ROOT / "verification/core/l1"
    if which == D:
        files = [RTL / FILES[P], L1_RAM, work / FILES[D], tests / "l1d_unit.sv", tests / "tb_l1d.cpp"]
        top, prefix = "l1d_unit", "Vl1d_unit"
    else:
        files = [L1_RAM, work / FILES[I], tests / "tb_l1i.cpp"]
        top, prefix = "aster_l1i", "Vaster_l1i"
    return ["verilator", "--cc", "--exe", "--build", "-Wno-fatal", "--top-module", top, "--prefix", prefix,
            "--Mdir", str(work / "unit_obj"), "-o", str(work / "unit"), *map(str, files)]


def verdict(sim: Path, work: Path, l1: bool, unit: Path | None = None) -> str:
    base = [sys.executable, str(ROOT / "scripts/run_core_tests.py"), "--dut", "aster_l1" if l1 else "aster",
            "--sim", str(sim), "--spike", SPIKE]
    for label, extra in stages(l1):
        shutil.rmtree(work / "t", ignore_errors=True)
        try:
            run = subprocess.run(base + extra + ["--build-dir", str(work / "t")], capture_output=True, text=True,
                                 timeout=1200, env={**os.environ, "RISCV_PREFIX": os.environ.get(
                                     "RISCV_PREFIX", "riscv32-unknown-elf-")})
        except subprocess.TimeoutExpired:
            return f"CAUGHT by {label}: runner timeout"
        if run.returncode:
            first = next((line for line in run.stdout.splitlines() if line.startswith("FAIL")),
                         (run.stdout + run.stderr).strip()[-150:])
            return f"CAUGHT by {label}: {' '.join(first.split())[:170]}"
    if unit is not None:
        for seed in range(1, UNIT_SEEDS + 1):
            run = subprocess.run([str(unit), str(seed), str(UNIT_CYCLES)], capture_output=True, text=True, timeout=600)
            if run.returncode or not run.stdout.startswith("PASS"):
                return f"CAUGHT by l1 unit: {' '.join((run.stdout + run.stderr).split())[:170]}"
    return "MISSED"


def one(out: Path, original: dict[str, str], name: str) -> str:
    which, old, new = MUTANTS[name]
    work = out / name
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    files = dict(original)
    files[which] = files[which].replace(old, new)
    for k, text in files.items():
        (work / FILES[k]).write_text(text)
    l1 = cached(name)
    rtl = [work / FILES[k] for k in (P, F, C)] + ([L1_RAM, work / FILES[I], work / FILES[D]] if l1 else [])
    build = subprocess.run(["verilator", "--cc", "--exe", "--build", "-Wno-fatal", "--top-module", "shell_aster_ports",
                            *(["-GL1=1"] if l1 else []),
                            "--prefix", "Vcore_ports", "--Mdir", str(work / "obj"), "-o", str(work / "sim"),
                            *map(str, rtl), str(ROOT / "verification/core/shell_aster_ports.sv"),
                            str(ROOT / "verification/core/tb_core_ports.cpp")],
                           capture_output=True, text=True)
    if build.returncode:
        shutil.rmtree(work, ignore_errors=True)
        return f"{name}: BUILD FAILED {' '.join(build.stderr[-200:].split())}"
    shutil.rmtree(work / "obj", ignore_errors=True)        # the simulator is all that is needed
    unit = None
    if which in (I, D):
        build = subprocess.run(unit_build(which, work), capture_output=True, text=True)
        if build.returncode:
            shutil.rmtree(work, ignore_errors=True)
            return f"{name}: BUILD FAILED (unit test) {' '.join(build.stderr[-200:].split())}"
        unit = work / "unit"
    try:
        return f"{name}: {verdict(work / 'sim', work, l1, unit)}"
    finally:
        shutil.rmtree(work, ignore_errors=True)            # keep the disk free: the verdict is the record


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1]).resolve()       # Verilator reads a relative -o against --Mdir
    names = sys.argv[2:] or list(MUTANTS)
    unknown = [name for name in names if name not in MUTANTS]
    if unknown:
        print(f"unknown mutants: {', '.join(unknown)}")
        return 2
    original = sources()
    stale = [name for name in names if original[MUTANTS[name][0]].count(MUTANTS[name][1]) != 1]
    if stale:
        print(f"anchors not found exactly once in the RTL: {', '.join(stale)}")
        return 2
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for line in pool.map(lambda name: one(out, original, name), names):
            print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
