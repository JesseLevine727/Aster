// Aster core: the seven-stage, single-issue, in-order core of docs/cpu.md —
// F1 F2 (aster_core_fetch), Decode, Execute, M1, M2, Write-back.
//
// Milestones 18.1-18.5: RV32IMA, Zicsr, Zifencei, Xasterdot8, machine-mode
// traps and interrupts, and the counters (aster_core_pkg; docs/cpu.md §3).
//
// Xasterdot8 (18.5): dot8 rd, rs1, rs2 — the sum of the four products of the
// operands' signed bytes — is computed in M1 from the operands Execute passed
// on, and its value enters M2 with it, so it is forwarded from M2 and W. A
// Decode instruction that reads it waits while the dot8 is in Execute (one
// cycle at distance 1); one in Execute waits while it is in M1.
//
// Atomics (18.4): an lr.w, sc.w or AMO is one data-port operation (d_req_op, a
// word, address rs1), executed by the memory as one transaction; its result
// (the word, sc's 0 or 1, the AMO's old value) comes back as a load's does,
// so it is a late result for the interlock. fence.i waits in Execute until M1
// and M2 are empty (every older data access has been answered) and then
// redirects to the next instruction like a jalr, which refetches everything
// after it.
//
// Traps (docs/cpu.md §3-§4): an instruction with a trap cause — a fetch fault
// it carries, an illegal encoding, `ecall` or `ebreak`, a misaligned access or
// jump target (all known in Execute), or a data-port error (known in M1) —
// traps at the commit point, the end of M1: mepc, mcause, mtval and mstatus
// are written, every younger instruction is killed, and the fetch unit is
// redirected to mtvec (direct mode) through its registered Execute-redirect
// path, which a trap overrides. The trapping instruction retires as a trap
// record (rvfi_trap) whose rvfi_pc_wdata is the handler's address.
//
// Interrupts: MEIP, MTIP and MSIP (registered from the inputs), enabled by mie
// and mstatus.MIE, are taken at the commit point in a cycle in which M1 holds a
// valid instruction: that instruction completes, the younger ones are killed,
// and mepc receives its next PC. They are not taken while M1 holds a CSR
// instruction or `mret` (which change the state that enables them); the
// instruction after it can be interrupted. The handler's first instruction
// retires with rvfi_intr.
//
// CSR instructions and `mret` are serializing: Execute holds one until M1 is
// empty, so it reads every older instruction's CSR effects and minstret counts
// exactly the older instructions (they have all passed the commit point). Its
// CSR write (and `mret`'s mstatus update, computed in Execute) takes place at
// the end of its first cycle in M1, and its own retirement is counted there
// too: nothing can kill it in M1 (it has no trap and no data access, and no
// interrupt is taken while it is there), so neither waits for M1 to advance, and no
// late signal reaches the CSR write enables (m1_first is registered). A write
// to minstret suppresses that instruction's own increment, and a write to
// mcountinhibit applies after it, as in Spike. `mret` redirects from Execute to
// mepc like a `jalr`.
//
// Pipeline (docs/cpu.md §4):
// - Decode: decode, the register file read (a same-cycle write-back is
//   forwarded, by selects registered a cycle ahead), the load-use interlock,
//   and the Decode redirect of a `jal` or a
//   backward branch (predicted taken) with an aligned target, fired once per
//   instruction on its first cycle in Decode.
// - Execute: operands forwarded from M1, M2 and W (a load's value only from W;
//   while Execute waits, it keeps the forwarded values so none is lost when its
//   producer retires); ALU, branch compare and targets, the data request. The
//   forwarding selects are computed a cycle ahead, from the next contents of
//   Execute, M1, M2 and W, and registered, so Execute's operand path is a
//   registered 4:1 mux.
//   A mispredicted branch or a `jalr` redirects at the edge where it leaves
//   Execute; the redirect is registered (aster_core_fetch presents the target
//   in the next cycle), and so is the flush: the instructions that entered
//   Decode and Execute at that edge are squashed — masked in that next cycle,
//   so they issue nothing, and gone after it — which keeps the branch compare
//   off the pipeline's register enables. A branch whose target is its own
//   fall-through never redirects, so the core's timing depends only on the
//   instruction stream (scripts/cpi_model.py models it exactly).
// - M1, M2: the data access's two memory stages. A request presented from
//   Execute is accepted at the Execute→M1 edge, its error is sampled in the
//   cycle after (held while M1 waits), and its answer is registered at the end
//   of M2 — an answer arriving in M1 (a one-cycle memory) is held until M2, and
//   M2 waits for a late one. The commit point is the end of M1.
// - W: the register write and the RVFI record, registered (it appears in the
//   cycle after W). A load's value is aligned and extended as it leaves M2, so
//   W's forwarded value comes straight from a register.
//
// Load-use: a Decode instruction that reads the result of a load or a multiply
// in Execute or M1 waits in Decode (2 or 1 cycles); a load's or multiply's
// result is forwarded from W. A store's data is read like any operand, so it
// waits the same way.
//
// M extension (18.2):
// - Multiply: pipelined over Execute, M1 and M2. Execute's operands travel
//   with the instruction (rs1v, rs2v); M1 forms four 17x17 signed partial
//   products of the 33-bit sign- or zero-extended operands (the FPGA's DSP
//   blocks), registered at its end; M2 adds them into the 64-bit product, whose
//   low or high word is registered into W. Its result is ready from W, as a
//   load's is.
// - Divide and remainder: an iterative radix-2 restoring divider in Execute,
//   which holds Execute for DIVIDE_CYCLES (36): the operands are latched in the
//   first cycle their values are ready, turned into magnitudes in the next, 32
//   steps follow, and a cycle registers the result with the sign the operands
//   require (a divisor of zero gives a quotient of all ones and the dividend as
//   remainder, as RISC-V specifies). Its result leaves Execute like an ALU
//   result, from a register.
//
// Data port: Execute presents a request only when M1 can take an instruction
// (M1 is empty or moving on) and M1 holds no trapping or interrupted
// instruction, so at most two requests are in flight (M1 and M2) and a waiting
// request stays stable until it is accepted: in the cycle after a request
// waited, M1 is empty, so no trap or interrupt is taken there.
//
// Verification: `chk_i_redirect` is high in a cycle in which the fetch unit may
// withdraw or replace an unaccepted fetch (a redirect's target presented); the
// core never withdraws a data request. The RVFI CSR fields (riscv-formal names)
// report every CSR an instruction reads or writes, with the value written as
// it reads back; a trap record reports mepc, mcause, mtval and mstatus as the
// trap left them, and `mret` its mstatus (and mstatush, the RV32 high half).
`timescale 1 ns / 1 ps
module aster_core
    import aster_core_pkg::*;
#(
    parameter logic [31:0] RESET_VECTOR = 32'h8000_0000,
    parameter logic [31:0] HART_ID = 32'd0
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        meip,
    input  logic        mtip,
    input  logic        msip,
    // instruction port
    output logic        i_req_valid,
    output logic [31:2] i_req_addr,
    input  logic        i_req_ready,
    input  logic        i_rsp_valid,
    input  logic [31:0] i_rsp_data,
    input  logic        i_rsp_error,
    // data port (d_req_op: aster_core_pkg's OP_* codes)
    output logic        d_req_valid,
    output logic [3:0]  d_req_op,
    output logic [31:0] d_req_addr,
    output logic [31:0] d_req_wdata,
    output logic [3:0]  d_req_be,
    input  logic        d_req_ready,
    input  logic        d_rsp_valid,
    input  logic [31:0] d_rsp_rdata,
    input  logic        d_rsp_error,
    // verification
    output logic        chk_i_redirect,
    // RVFI (riscv-formal names, RISCV_FORMAL_ALIGNED_MEM layout, registered)
    output logic        rvfi_valid,
    output logic [63:0] rvfi_order,
    output logic [31:0] rvfi_insn,
    output logic        rvfi_trap,
    output logic        rvfi_halt,
    output logic        rvfi_intr,
    output logic [1:0]  rvfi_mode,
    output logic [1:0]  rvfi_ixl,
    output logic [4:0]  rvfi_rs1_addr,
    output logic [4:0]  rvfi_rs2_addr,
    output logic [31:0] rvfi_rs1_rdata,
    output logic [31:0] rvfi_rs2_rdata,
    output logic [4:0]  rvfi_rd_addr,
    output logic [31:0] rvfi_rd_wdata,
    output logic [31:0] rvfi_pc_rdata,
    output logic [31:0] rvfi_pc_wdata,
    output logic [31:0] rvfi_mem_addr,
    output logic [3:0]  rvfi_mem_rmask,
    output logic [3:0]  rvfi_mem_wmask,
    output logic [31:0] rvfi_mem_rdata,
    output logic [31:0] rvfi_mem_wdata,
    // RVFI CSR fields: read and write masks and data per CSR (riscv-formal
    // names; the counters are 64-bit, a write to an RV32 half masking that half)
    output logic [31:0] rvfi_csr_mstatus_rmask, rvfi_csr_mstatus_wmask, rvfi_csr_mstatus_rdata, rvfi_csr_mstatus_wdata,
    output logic [31:0] rvfi_csr_mstatush_rmask, rvfi_csr_mstatush_wmask, rvfi_csr_mstatush_rdata, rvfi_csr_mstatush_wdata,
    output logic [31:0] rvfi_csr_misa_rmask, rvfi_csr_misa_wmask, rvfi_csr_misa_rdata, rvfi_csr_misa_wdata,
    output logic [31:0] rvfi_csr_mie_rmask, rvfi_csr_mie_wmask, rvfi_csr_mie_rdata, rvfi_csr_mie_wdata,
    output logic [31:0] rvfi_csr_mip_rmask, rvfi_csr_mip_wmask, rvfi_csr_mip_rdata, rvfi_csr_mip_wdata,
    output logic [31:0] rvfi_csr_mtvec_rmask, rvfi_csr_mtvec_wmask, rvfi_csr_mtvec_rdata, rvfi_csr_mtvec_wdata,
    output logic [31:0] rvfi_csr_mscratch_rmask, rvfi_csr_mscratch_wmask, rvfi_csr_mscratch_rdata, rvfi_csr_mscratch_wdata,
    output logic [31:0] rvfi_csr_mepc_rmask, rvfi_csr_mepc_wmask, rvfi_csr_mepc_rdata, rvfi_csr_mepc_wdata,
    output logic [31:0] rvfi_csr_mcause_rmask, rvfi_csr_mcause_wmask, rvfi_csr_mcause_rdata, rvfi_csr_mcause_wdata,
    output logic [31:0] rvfi_csr_mtval_rmask, rvfi_csr_mtval_wmask, rvfi_csr_mtval_rdata, rvfi_csr_mtval_wdata,
    output logic [31:0] rvfi_csr_mcountinhibit_rmask, rvfi_csr_mcountinhibit_wmask, rvfi_csr_mcountinhibit_rdata,
                        rvfi_csr_mcountinhibit_wdata,
    output logic [63:0] rvfi_csr_mcycle_rmask, rvfi_csr_mcycle_wmask, rvfi_csr_mcycle_rdata, rvfi_csr_mcycle_wdata,
    output logic [63:0] rvfi_csr_minstret_rmask, rvfi_csr_minstret_wmask, rvfi_csr_minstret_rdata,
                        rvfi_csr_minstret_wdata
);
    // An instruction in M1, M2 or W.
    typedef struct packed {
        logic        valid;
        logic        trap;
        logic        writes_rd;
        logic        load;
        logic        store;
        logic        mul;
        logic        late;          // its value is not ready in M1: a load or a multiply (ready only in W), or a dot8 (ready in M2)
        logic        dot8;          // Xasterdot8's dot8: its value is computed in M1 and enters M2 with it
        logic        acc;           // its data request was accepted
        logic        got;           // its answer has arrived
        logic        sys;           // a CSR instruction or mret: no interrupt is taken after it in M1
        logic        csr_rd;        // a CSR instruction (RVFI: it read csr_sel)
        logic        csr_we;        // it writes csr_sel (a CSR write, or mret's mstatus) with wdata
        logic        mret;
        logic        intr;          // the first instruction of a trap handler (RVFI)
        logic [3:0]  cause;         // the exception code (of a trap known in Execute; from M2 on, the trap's)
        logic        mpie;          // RVFI, a trap record: the MPIE its trap left (MIE before it)
        csr_t        csr_sel;
        logic [2:0]  funct3;
        logic [4:0]  rd;
        logic [4:0]  rs1;
        logic [4:0]  rs2;
        logic [3:0]  be;
        logic [31:0] pc;
        logic [31:0] next_pc;
        logic [31:0] insn;
        logic [31:0] result;        // ALU or link result
        logic [31:0] addr;          // the access's byte address
        logic [31:0] wdata;         // store data in its byte lanes, or the CSR value to write
        logic [31:0] rs1v;
        logic [31:0] rs2v;
        logic [31:0] rdata;         // the aligned word read
        logic [31:0] tval;          // RVFI, a trap record: its mtval
    } slot_t;

    logic squash;                   // an Execute redirect resolved at the last edge: Decode and Execute are wrong-path
    logic intr_next;                // a trap or interrupt was taken: the next instruction to leave Execute starts its handler

    // ------------------------------------------------------------------- CSRs
    logic        mstatus_mie, mstatus_mpie;
    logic        mie_meie, mie_mtie, mie_msie;
    logic        cy_inhibit, ir_inhibit;   // mcountinhibit.CY and .IR
    logic [31:2] mtvec, mepc;
    logic [31:0] mscratch, mcause, mtval;
    logic [2:0]  mip_q;                    // {MEIP, MTIP, MSIP}, registered from the inputs
    logic [63:0] mcycle, minstret;
    logic [63:0] time_count;               // time/timeh: one tick per clock from reset, never written or stopped
    logic [31:0] mstatus_value, mie_value, mip_value, mcountinhibit_value;
    assign mstatus_value       = {19'b0, 2'b11, 3'b0, mstatus_mpie, 3'b0, mstatus_mie, 3'b0};   // MPP reads M
    assign mie_value           = {20'b0, mie_meie, 3'b0, mie_mtie, 3'b0, mie_msie, 3'b0};
    assign mip_value           = {20'b0, mip_q[2], 3'b0, mip_q[1], 3'b0, mip_q[0], 3'b0};
    assign mcountinhibit_value = {29'b0, ir_inhibit, 1'b0, cy_inhibit};
    localparam logic [31:0] MISA = 32'h4080_1101;       // RV32, I, M, A and X (Xasterdot8)

    // ------------------------------------------------------------------ fetch
    logic        d_redirect, e_flush, f_valid, f_error, d_take;
    logic [31:2] f_pc;
    logic [31:0] f_insn, e_next_pc, e_redirect_target;
    logic [31:2] d_target;          // the Decode redirect's target (predecoded)
    // The fetch unit's registered redirect: Execute's, or a trap's or an
    // interrupt's at the commit point (they never coincide: Execute does not
    // advance while M1 traps or is interrupted, so m1_stop selects the target).
    logic        kill, m1_stop, f_redirect;
    logic [31:2] f_target;
    assign f_redirect = e_flush || kill;
    assign f_target   = m1_stop ? mtvec : e_redirect_target[31:2];

    aster_core_fetch #(.RESET_VECTOR(RESET_VECTOR)) fetch (
        .clk, .rst_n,
        .i_req_valid, .i_req_addr, .i_req_ready, .i_rsp_valid, .i_rsp_data, .i_rsp_error,
        .d_redirect, .d_target, .e_flush(f_redirect), .e_target(f_target),
        .redirecting(chk_i_redirect),
        .f_valid, .f_pc, .f_insn, .f_error, .d_take
    );

    // --------------------------------------------------------- register file
    logic [31:0] rf [1:31];
    slot_t       w;
    logic        w_write;
    logic [31:0] w_value;

    // Decode's reads, indexed straight from the instruction's register fields.
    // A same-cycle write-back is forwarded by the registered selects wt1/wt2
    // (computed a cycle ahead, below), so no rd/rs compare sits on this path.
    // (A fetch fault's operands are never used, so they need no masking.)
    logic       wt1, wt2;
    logic [4:0] d_rs1_index, d_rs2_index;
    logic [31:0] d_rs1v, d_rs2v;
    assign d_rs1_index = d_insn[19:15];
    assign d_rs2_index = d_insn[24:20];
    assign d_rs1v = d_rs1_index == 5'd0 ? 32'b0 : wt1 ? w_value : rf[d_rs1_index];
    assign d_rs2v = d_rs2_index == 5'd0 ? 32'b0 : wt2 ? w_value : rf[d_rs2_index];

    // ----------------------------------------------------------------- Decode
    logic        d_valid, d_err, d_redirected;
    logic [31:0] d_pc, d_insn;
    decoded_t    d_raw, d_dec;
    logic        d_predict;
    logic [32:0] f_predecode;       // {predict, target} of the instruction the fetch unit offers

    logic d_live;                   // Decode holds an instruction that is not squashed
    assign d_live   = d_valid && !squash;
    assign d_raw    = decode(d_insn);
    assign d_dec    = d_err ? decoded_t'('0) : d_raw;     // a fetch fault carries no operation
    // jal, and a backward branch, redirect from Decode (the target must be
    // aligned). The decision and the target are predecoded as the instruction
    // enters Decode and registered (d_predict, d_target), so the redirect is
    // presented from registered state in the instruction's first Decode cycle.
    assign f_predecode = predecode(f_insn, {f_pc, 2'b00});
    assign d_redirect  = d_live && d_predict && !d_redirected;

    // --------------------------------------------------------------- Execute
    logic        e_valid, e_err, e_pred;
    logic [31:0] e_pc, e_insn, e_rs1v, e_rs2v;
    decoded_t    e_dec;
    slot_t       m1, m2;
    logic        e_live;             // Execute holds an instruction that is not squashed
    assign e_live = e_valid && !squash;

    // Forwarding from the newest older instruction that writes the register, by
    // the registered one-hot selects (computed below, a cycle ahead).
    logic [3:0]  fsel1, fsel2;      // one-hot {M1, M2, W, none}
    // Inverted copies of the selects for Execute's stall logic alone: rs1's
    // drives the operand's two low bits (alignment and byte enables) and both
    // drive readiness, so whether Execute advances does not wait behind the
    // 32-bit operand mux's fanout. They hold the complement so that synthesis
    // cannot merge them into the selects (a `keep`-marked copy was merged).
    logic [3:0]  fsel1_n;
    logic [3:2]  fsel2_n;
    logic [31:0] rs1f, rs2f;
    logic [1:0]  rs1f_lo;
    logic        rs1_ready, rs2_ready;
    // The select is one-hot (none included), so the operand is an AND-OR of
    // the four sources with no decode in front of the fanout.
    function automatic logic [31:0] forward(input logic [3:0] sel, input logic [31:0] held);
        return ({32{sel[3]}} & m1.result) | ({32{sel[2]}} & m2.result) |
               ({32{sel[1]}} & w_value)   | ({32{sel[0]}} & held);
    endfunction
    assign rs1f    = forward(fsel1, e_rs1v);
    assign rs2f    = forward(fsel2, e_rs2v);
    assign rs1f_lo = ({2{!fsel1_n[3]}} & m1.result[1:0]) | ({2{!fsel1_n[2]}} & m2.result[1:0]) |
                     ({2{!fsel1_n[1]}} & w_value[1:0])   | ({2{!fsel1_n[0]}} & e_rs1v[1:0]);
    // A load's or multiply's value is not ready before W.
    assign rs1_ready = (fsel1_n[3] || !m1.late) && (fsel1_n[2] || !m2.late);
    assign rs2_ready = (fsel2_n[3] || !m1.late) && (fsel2_n[2] || !m2.late);

    logic        e_ready, e_taken, e_misaligned, e_target_misaligned, e_trap, e_access, e_mispredict;
    logic [31:0] e_addr, e_link, e_result, e_btarget, e_jtarget, e_wdata;
    logic [1:0]  e_offset;
    logic [3:0]  e_be, e_cause;
    logic        e_operands;        // the operands' values are ready
    logic        div_done;          // the divider holds Execute's division result
    assign e_operands = (!e_dec.uses_rs1 || rs1_ready) && (!e_dec.uses_rs2 || rs2_ready);
    // A CSR instruction or mret waits until M1 is empty (serializing).
    assign e_ready    = e_operands && (!e_dec.div || div_done) && (!e_dec.sys || !m1.valid)
                      && (!e_dec.fencei || (!m1.valid && !m2.valid));

    // Divider: div_count is 0 while idle; the operands as read are latched as it
    // starts (count 0 to 1), so no arithmetic follows the forwarding mux; count
    // 1 turns them into magnitudes (and notes the signs and a zero divisor);
    // counts 2-33 are the 32 steps; count 34 registers the result with its sign
    // (div_result), so the result leaves Execute from a register; count 35 is
    // done. A division holds Execute for DIVIDE_CYCLES = 36.
    logic [5:0]  div_count;
    logic [31:0] div_remainder, div_quotient, div_divisor, div_result;
    logic        div_signed, div_negate_q, div_negate_r, div_by_zero, div_rem;
    logic        div_start, div_signs, div_stepping, div_finish;
    logic [63:0] div_next;
    assign div_start    = e_live && e_dec.div && e_operands && div_count == 6'd0;
    assign div_signs    = div_count == 6'd1;
    assign div_stepping = div_count >= 6'd2 && div_count <= 6'd33;
    assign div_finish   = div_count == 6'd34;
    assign div_next     = divide_step(div_remainder, div_quotient, div_divisor);

    // The result is one-hot selected (e_dec.res, decoded in Decode): no decode
    // follows the forwarded operands, and operand A is rs1 alone (auipc takes
    // the branch-target adder's PC plus immediate; lui takes the immediate).
    // CSR read and modify. Execute holds a CSR instruction until M1 is empty,
    // so the registers hold every older instruction's effects. The new value
    // (or mret's mstatus) travels to M1 in the slot's wdata.
    logic [31:0] csr_rdata, csr_src, csr_new, mret_status;
    assign csr_rdata = ({32{e_dec.csr_sel.mstatus}}       & mstatus_value)
                     | ({32{e_dec.csr_sel.misa}}          & MISA)
                     | ({32{e_dec.csr_sel.mie}}           & mie_value)
                     | ({32{e_dec.csr_sel.mtvec}}         & {mtvec, 2'b00})
                     | ({32{e_dec.csr_sel.mcountinhibit}} & mcountinhibit_value)
                     | ({32{e_dec.csr_sel.mscratch}}      & mscratch)
                     | ({32{e_dec.csr_sel.mepc}}          & {mepc, 2'b00})
                     | ({32{e_dec.csr_sel.mcause}}        & mcause)
                     | ({32{e_dec.csr_sel.mtval}}         & mtval)
                     | ({32{e_dec.csr_sel.mip}}           & mip_value)
                     | ({32{e_dec.csr_sel.mcycle}}        & mcycle[31:0])
                     | ({32{e_dec.csr_sel.mcycleh}}       & mcycle[63:32])
                     | ({32{e_dec.csr_sel.minstret}}      & minstret[31:0])
                     | ({32{e_dec.csr_sel.minstreth}}     & minstret[63:32])
                     | ({32{e_dec.csr_sel.time_lo}}       & time_count[31:0])
                     | ({32{e_dec.csr_sel.time_hi}}       & time_count[63:32])
                     | ({32{e_dec.csr_sel.mhartid}}       & HART_ID);       // mstatush and zero read 0
    assign csr_src   = e_dec.uses_rs1 ? rs1f : e_dec.imm;           // rs1, or a csrr*i's uimm
    assign csr_new   = e_dec.funct3[1:0] == 2'd1 ? csr_src
                     : e_dec.funct3[1:0] == 2'd2 ? csr_rdata | csr_src : csr_rdata & ~csr_src;
    assign mret_status = {19'b0, 2'b11, 3'b0, 1'b1, 3'b0, mstatus_mpie, 3'b0};   // MIE = MPIE, MPIE = 1

    assign e_link    = e_pc + 32'd4;
    assign e_result  = result(e_dec.res, rs1f, e_dec.b_imm ? e_dec.imm : rs2f, e_dec.imm, e_btarget, e_link,
                              div_result, csr_rdata);
    assign e_taken   = e_dec.branch && branch_taken(e_dec.funct3, rs1f, rs2f);
    assign e_btarget = e_pc + e_dec.imm;
    assign e_jtarget = {e_addr[31:1], 1'b0};       // rs1 + imm, from the address adder (not the ALU's mux)
    assign e_next_pc = e_dec.mret ? {mepc, 2'b00} : e_dec.jalr ? e_jtarget
                     : (e_dec.jal || e_taken) ? e_btarget : e_link;
    // When Execute redirects, its target is known without the compare: mepc for
    // `mret`, a `jalr`'s computed target, else the fall-through if Decode
    // predicted the branch taken, else the branch target. The compare only
    // decides whether.
    assign e_redirect_target = e_dec.mret ? {mepc, 2'b00} : e_dec.jalr ? e_jtarget
                             : (e_pred || e_dec.fencei) ? e_link : e_btarget;
    assign e_target_misaligned = (e_dec.jalr && e_jtarget[1]) || ((e_dec.jal || e_taken) && e_btarget[1]);
    // A load's or store's address (and a jalr's target) has its own adder, and
    // its alignment comes from the two low bits alone, so the stall logic does
    // not wait for a 32-bit sum. (A fetch fault or an illegal encoding decodes
    // as no access.)
    assign e_addr    = rs1f + e_dec.imm;
    assign e_offset  = rs1f_lo + e_dec.imm[1:0];
    assign e_misaligned = (e_dec.load || e_dec.store) &&
                          ((e_dec.funct3[1:0] == 2'd1 && e_offset[0]) || (e_dec.funct3[1:0] == 2'd2 && e_offset != 2'd0));
    assign e_trap    = e_err || e_dec.illegal || e_dec.ecall || e_dec.ebreak || e_misaligned || e_target_misaligned;
    // The exception code (only one cause can apply: a fetch fault decodes as
    // nothing, an illegal encoding as no operation).
    assign e_cause   = e_err ? 4'd1 : e_dec.illegal ? 4'd2 : e_dec.ebreak ? 4'd3 : e_dec.ecall ? 4'd11
                     : e_target_misaligned ? 4'd0 : e_dec.store ? 4'd6 : 4'd4;
    assign e_access  = (e_dec.load || e_dec.store) && !e_misaligned;
    assign e_be      = e_dec.funct3[1:0] == 2'd0 ? 4'b0001 << e_offset
                     : e_dec.funct3[1:0] == 2'd1 ? 4'b0011 << e_offset : 4'b1111;
    assign e_wdata   = e_dec.funct3[1:0] == 2'd0 ? {4{rs2f[7:0]}}
                     : e_dec.funct3[1:0] == 2'd1 ? {2{rs2f[15:0]}} : rs2f;
    // A branch to its own fall-through (offset +4) never redirects: its direction
    // does not change the next PC. The trap logic is left out of this path: a
    // branch or jalr that traps (a misaligned target) traps at the commit
    // point, whose redirect replaces its own.
    assign e_mispredict = e_dec.jalr || e_dec.mret || e_dec.fencei
                       || (e_dec.branch && e_taken != e_pred && e_dec.imm != 32'd4);

    // ------------------------------------------------------------ M1, M2, W
    logic d_acc_last;               // a data request was accepted at the last edge (it is in M1)
    logic m1_err_held;
    logic m1_bus_err, m1_trap, m1_irq;
    logic rsp_for_m2, rsp_for_m1;
    slot_t m1_view, m2_view;

    assign m1_bus_err = m1.acc && (d_acc_last ? d_rsp_error : m1_err_held);
    assign m1_trap    = m1.valid && (m1.trap || m1_bus_err);

    // Interrupts: pending and enabled, taken after the instruction in M1 (from
    // registered state only). MEI has priority over MSI over MTI.
    logic       irq_pending;
    logic [3:0] irq_code;
    assign irq_pending = mstatus_mie && ((mip_q[2] && mie_meie) || (mip_q[1] && mie_mtie) || (mip_q[0] && mie_msie));
    assign irq_code    = mip_q[2] && mie_meie ? 4'd11 : mip_q[0] && mie_msie ? 4'd3 : 4'd7;
    assign m1_irq      = m1.valid && !m1.sys && irq_pending;
    // M1 stops Execute: its instruction traps, or an interrupt is taken after it.
    assign m1_stop     = m1_trap || m1_irq;

    // The exception M1's instruction takes, and its mtval.
    logic [3:0]  trap_cause;
    logic [31:0] trap_tval;
    assign trap_cause = m1.trap ? m1.cause : m1.store ? 4'd7 : 4'd5;     // else a data-port error
    always_comb begin
        unique case (trap_cause)
            4'd0:                   trap_tval = m1.next_pc;    // the misaligned jump or branch target
            4'd1, 4'd3:             trap_tval = m1.pc;         // fetch fault, ebreak
            4'd2:                   trap_tval = m1.insn;       // the illegal instruction
            4'd4, 4'd5, 4'd6, 4'd7: trap_tval = m1.addr;       // the access's address
            default:                trap_tval = 32'b0;         // ecall
        endcase
    end
    assign rsp_for_m2 = m2.valid && m2.acc && !m2.got;
    assign rsp_for_m1 = !rsp_for_m2 && m1.valid && m1.acc && !m1.got;

    always_comb begin
        m1_view = m1;
        if (d_rsp_valid && rsp_for_m1) begin            // an answer in M1, held until M2
            m1_view.got   = 1'b1;
            m1_view.rdata = d_rsp_rdata;
        end
        m2_view = m2;
        if (d_rsp_valid && rsp_for_m2) begin
            m2_view.got   = 1'b1;
            m2_view.rdata = d_rsp_rdata;
        end
    end

    // --------------------------------------------------------- advancement
    logic m2_advance, m2_free, m1_advance, m1_free, e_advance, e_free, d_advance, d_free, d_hazard;
    assign m2_advance = m2.valid && (!m2.acc || m2_view.got);
    assign m2_free    = !m2.valid || m2_advance;
    assign m1_advance = m1.valid && m2_free;
    assign m1_free    = !m1.valid || m1_advance;
    assign kill       = m1_stop && m1_advance;          // a trap or an interrupt is taken at this edge

    assign d_req_valid = e_live && e_access && e_ready && m1_free && !m1_stop;
    assign d_req_op    = e_dec.atomic ? e_dec.amo_op : e_dec.store ? OP_STORE : OP_LOAD;
    assign d_req_addr  = e_addr;
    assign d_req_wdata = e_wdata;
    assign d_req_be    = e_be;

    assign e_advance = e_live && e_ready && m1_free && !m1_stop && (!e_access || d_req_ready);
    assign e_free    = !e_live || e_advance;
    assign e_flush   = e_advance && e_mispredict;

    // Load-use interlock: the newest of Execute and M1 writing the register is a
    // load or a multiply, or Execute's is a dot8 (in M1, a dot8's value reaches
    // M2 by the time the reader is in Execute; if M1 waits, Execute waits).
    function automatic logic load_pending(input logic [4:0] r);
        if (r == 5'd0) return 1'b0;
        if (e_live && e_dec.writes_rd && e_dec.rd == r) return e_dec.load || e_dec.mul || e_dec.dot8;
        return m1.valid && m1.writes_rd && m1.rd == r && m1.late && !m1.dot8;
    endfunction
    assign d_hazard  = (d_dec.uses_rs1 && load_pending(d_dec.rs1)) || (d_dec.uses_rs2 && load_pending(d_dec.rs2));
    assign d_advance = d_live && e_free && !d_hazard;
    assign d_free    = !d_live || d_advance;
    assign d_take    = d_free;

    // ------------------------------------------------------------ multiplier
    // M1: the operands, sign-extended for mulh (both), mulhsu (rs1) and mul (its
    // low word does not depend on it), zero-extended otherwise, split into
    // 17-bit signed halves (the low halves zero-extended), give four 17x17
    // partial products (the FPGA's DSP blocks), registered for M2. M2: their sum
    // is the 64-bit product.
    function automatic logic signed [33:0] partial(input logic [16:0] a, input logic [16:0] b);
        return $signed(a) * $signed(b);
    endfunction
    logic [32:0] mul_a, mul_b;
    logic signed [33:0] pp_ll, pp_lh, pp_hl, pp_hh;     // registered at the end of M1, for M2
    logic [63:0] mul_product;
    logic [31:0] mul_result;
    assign mul_a = {m1.funct3[1:0] != 2'd3 && m1.rs1v[31], m1.rs1v};
    assign mul_b = {m1.funct3[1] == 1'b0 && m1.rs2v[31], m1.rs2v};
    assign mul_product = 64'($signed(pp_ll)) + (64'($signed(pp_lh)) << 16) + (64'($signed(pp_hl)) << 16)
                       + (64'($signed(pp_hh)) << 32);
    assign mul_result  = m2.funct3[1:0] == 2'd0 ? mul_product[31:0] : mul_product[63:32];

    // ------------------------------------------------------------ Xasterdot8
    // M1: the four products of the operands' signed bytes (in DSP blocks: in
    // LUTs, product and sum together exceeded the cycle), summed in the fabric
    // (a sum chained through the DSPs' cascade was slower still) from -65,024
    // to 65,536 and sign-extended; the value enters M2 with the dot8.
    (* use_dsp = "yes" *) logic signed [15:0] dot8_p0, dot8_p1, dot8_p2, dot8_p3;
    (* use_dsp = "no" *)  logic signed [17:0] dot8_sum;
    assign dot8_p0  = $signed(m1.rs1v[7:0])   * $signed(m1.rs2v[7:0]);
    assign dot8_p1  = $signed(m1.rs1v[15:8])  * $signed(m1.rs2v[15:8]);
    assign dot8_p2  = $signed(m1.rs1v[23:16]) * $signed(m1.rs2v[23:16]);
    assign dot8_p3  = $signed(m1.rs1v[31:24]) * $signed(m1.rs2v[31:24]);
    assign dot8_sum = 18'(dot8_p0) + 18'(dot8_p1) + 18'(dot8_p2) + 18'(dot8_p3);

    // ------------------------------------------------------------ W values
    // A load's value is aligned and extended as it leaves M2, so W forwards and
    // writes a register (w.result); w.rdata keeps the word read, for RVFI.
    assign w_value = w.result;
    assign w_write = w.valid && w.writes_rd;

    // ------------------------------------------------ forwarding selects
    // The next contents of Execute, M1, M2 and W, as the sequential block below
    // loads them (a trapping producer is not excluded: whatever reads it is
    // younger and never commits).
    logic [4:0] en_rs1, en_rs2, m1n_rd, m2n_rd, wn_rd;
    logic       m1n_writes, m2n_writes, wn_writes;
    always_comb begin
        en_rs1 = e_free ? d_dec.rs1 : e_dec.rs1;
        en_rs2 = e_free ? d_dec.rs2 : e_dec.rs2;
        m1n_rd = m1.rd;
        m1n_writes = m1.valid && m1.writes_rd;
        if (kill) m1n_writes = 1'b0;
        else if (m1_free) begin
            m1n_rd     = e_dec.rd;
            m1n_writes = e_advance && e_dec.writes_rd;
        end
        m2n_rd = m2.rd;
        m2n_writes = m2.valid && m2.writes_rd;
        if (m2_free) begin
            m2n_rd     = m1.rd;
            m2n_writes = m1_advance && m1.writes_rd;
        end
        wn_rd     = m2.rd;
        wn_writes = m2_advance && m2.writes_rd;
    end
    // The register-file write-through selects: next cycle's W writes the
    // register next cycle's Decode instruction reads.
    logic [4:0] dn_rs1, dn_rs2;
    assign dn_rs1 = d_free ? f_insn[19:15] : d_insn[19:15];
    assign dn_rs2 = d_free ? f_insn[24:20] : d_insn[24:20];
    logic [3:0] sel1_next, sel2_next;
    function automatic logic [3:0] select(input logic [4:0] r);
        if (r == 5'd0) return 4'b0001;
        if (m1n_writes && m1n_rd == r) return 4'b1000;
        if (m2n_writes && m2n_rd == r) return 4'b0100;
        if (wn_writes && wn_rd == r) return 4'b0010;
        return 4'b0001;
    endfunction
    assign sel1_next = select(en_rs1);
    assign sel2_next = select(en_rs2);

    // ----------------------------------------------------------- sequential
    slot_t e_slot;
    always_comb begin
        e_slot           = '0;
        e_slot.valid     = 1'b1;
        e_slot.trap      = e_trap;
        e_slot.writes_rd = e_dec.writes_rd && !e_trap;
        e_slot.load      = e_dec.load && !e_trap;
        e_slot.store     = e_dec.store && !e_trap;
        e_slot.mul       = e_dec.mul && !e_trap;
        e_slot.late      = (e_dec.load || e_dec.mul || e_dec.dot8) && !e_trap;
        e_slot.dot8      = e_dec.dot8 && !e_trap;
        e_slot.acc       = e_access;
        e_slot.sys       = e_dec.sys;
        e_slot.csr_rd    = e_dec.csr && !e_trap;
        e_slot.csr_we    = (e_dec.csr_write || e_dec.mret) && !e_trap;
        e_slot.mret      = e_dec.mret && !e_trap;
        e_slot.intr      = intr_next;
        e_slot.cause     = e_cause;
        e_slot.csr_sel   = e_dec.csr_sel;
        e_slot.csr_sel.mstatus = e_dec.csr_sel.mstatus || e_dec.mret;
        e_slot.funct3    = e_dec.funct3;
        e_slot.rd        = e_dec.rd;
        e_slot.rs1       = e_dec.uses_rs1 ? e_dec.rs1 : 5'd0;
        e_slot.rs2       = e_dec.uses_rs2 ? e_dec.rs2 : 5'd0;
        e_slot.rs1v      = e_dec.uses_rs1 ? rs1f : 32'b0;
        e_slot.rs2v      = e_dec.uses_rs2 ? rs2f : 32'b0;
        e_slot.be        = e_access ? e_be : 4'b0;
        e_slot.pc        = e_pc;
        e_slot.next_pc   = e_next_pc;
        e_slot.insn      = e_insn;
        e_slot.result    = e_result;
        e_slot.addr      = e_addr;
        e_slot.wdata     = e_dec.csr ? csr_new : e_dec.mret ? mret_status : e_wdata;
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            squash       <= 1'b0;
            intr_next    <= 1'b0;
            fsel1        <= 4'b0001;
            fsel2        <= 4'b0001;
            fsel1_n      <= 4'b1110;
            fsel2_n      <= 2'b11;
            wt1          <= 1'b0;
            wt2          <= 1'b0;
            d_valid      <= 1'b0;
            d_redirected <= 1'b0;
            d_predict    <= 1'b0;
            e_valid      <= 1'b0;
            m1           <= '0;
            m2           <= '0;
            w            <= '0;
            d_acc_last   <= 1'b0;
            m1_err_held  <= 1'b0;
            div_count    <= 6'd0;
            div_done     <= 1'b0;
        end else begin
            squash     <= e_flush;
            if (kill) intr_next <= 1'b1;
            else if (e_advance) intr_next <= 1'b0;
            d_acc_last <= d_req_valid && d_req_ready;
            fsel1      <= sel1_next;
            fsel2      <= sel2_next;
            fsel1_n    <= ~sel1_next;
            fsel2_n    <= ~sel2_next[3:2];
            wt1        <= wn_writes && wn_rd == dn_rs1;
            wt2        <= wn_writes && wn_rd == dn_rs2;

            // Decode (a squashed instruction leaves: Decode is free)
            if (kill) begin
                d_valid <= 1'b0;
            end else if (d_free) begin
                d_valid      <= f_valid;
                d_pc         <= {f_pc, 2'b00};
                d_insn       <= f_insn;
                d_err        <= f_error;
                d_redirected <= 1'b0;
                d_predict    <= f_predecode[32] && !f_error;
                d_target     <= f_predecode[31:2];
            end else if (d_redirect) begin
                d_redirected <= 1'b1;
            end

            // Execute (keeps forwarded operands while it waits)
            if (kill) begin
                e_valid <= 1'b0;
            end else if (e_free) begin
                e_valid <= d_advance;
                e_pc    <= d_pc;
                // A 16-bit encoding (illegal: there is no C) is the instruction's
                // 16 bits, for its mtval and RVFI record (as Spike reports it).
                e_insn  <= {d_insn[1:0] == 2'b11 ? d_insn[31:16] : 16'b0, d_insn[15:0]};
                e_err   <= d_err;
                e_dec   <= d_dec;
                e_pred  <= d_predict;
                e_rs1v  <= d_rs1v;
                e_rs2v  <= d_rs2v;
            end else begin
                if (rs1_ready) e_rs1v <= rs1f;
                if (rs2_ready) e_rs2v <= rs2f;
            end

            // Divider: its count restarts with each instruction Execute takes;
            // the data registers load on a start and step while the count runs,
            // so they are off the kill and Execute's advance (their values
            // matter only while the count runs).
            if (kill || e_free) begin
                div_count <= 6'd0;
                div_done  <= 1'b0;
            end else if (div_start) begin
                div_count <= 6'd1;
            end else if (div_count != 6'd0 && div_count <= 6'd34) begin
                div_count <= div_count + 6'd1;
                div_done  <= div_finish;
            end
            if (div_start) begin
                div_quotient  <= rs1f;
                div_divisor   <= rs2f;
                div_signed    <= !e_dec.funct3[0];
                div_rem       <= e_dec.funct3[1];
            end else if (div_signs) begin
                div_remainder <= 32'b0;
                div_quotient  <= (div_signed && div_quotient[31]) ? -div_quotient : div_quotient;
                div_divisor   <= (div_signed && div_divisor[31]) ? -div_divisor : div_divisor;
                div_negate_q  <= div_signed && (div_quotient[31] ^ div_divisor[31]);
                div_negate_r  <= div_signed && div_quotient[31];
                div_by_zero   <= div_divisor == 32'b0;
            end else if (div_stepping) begin
                {div_remainder, div_quotient} <= div_next;
            end else if (div_finish) begin
                div_result <= div_rem      ? (div_negate_r ? -div_remainder : div_remainder)
                            : div_by_zero  ? 32'hFFFF_FFFF
                            : div_negate_q ? -div_quotient : div_quotient;
            end

            // M1
            if (kill) begin
                m1 <= '0;
            end else if (m1_free) begin
                m1 <= e_advance ? e_slot : '0;
            end else begin
                m1 <= m1_view;
            end
            // M1's data fields load whenever M1 is free, valid or not: m1.valid
            // qualifies them (an M1 that is not valid is never forwarded from,
            // multiplied, or moved on), so Execute's late advance and the kill
            // stay off their inputs.
            if (m1_free) begin
                m1.result  <= e_slot.result;
                m1.addr    <= e_slot.addr;
                m1.wdata   <= e_slot.wdata;
                m1.rs1v    <= e_slot.rs1v;
                m1.rs2v    <= e_slot.rs2v;
                m1.pc      <= e_slot.pc;
                m1.next_pc <= e_slot.next_pc;
                m1.insn    <= e_slot.insn;
            end
            m1_err_held <= m1_bus_err;

            // The multiplier's partial products, for the multiply in M1, as it moves
            // into M2. They load whenever M1 holds a multiply, not on M1's advance
            // (which waits on the data port's answer): a multiply never waits in
            // M2, so the products of the one there are never overwritten.
            if (m1.mul) begin
                pp_ll <= partial({1'b0, mul_a[15:0]}, {1'b0, mul_b[15:0]});
                pp_lh <= partial({1'b0, mul_a[15:0]}, mul_b[32:16]);
                pp_hl <= partial(mul_a[32:16], {1'b0, mul_b[15:0]});
                pp_hh <= partial(mul_a[32:16], mul_b[32:16]);
            end

            // M2 (the commit point has passed: a data-port error becomes the trap,
            // and a trap record's next PC is its handler's address)
            if (m2_free) begin
                m2 <= '0;
                if (m1_advance) begin
                    m2           <= m1_view;
                    m2.trap      <= m1.trap || m1_bus_err;
                    m2.writes_rd <= m1.writes_rd && !m1_bus_err;
                    m2.load      <= m1.load && !m1_bus_err;
                    m2.late      <= m1.late && !m1.dot8 && !m1_bus_err;
                    if (m1.dot8) m2.result <= 32'($signed(dot8_sum));
                    m2.store     <= m1.store && !m1_bus_err;
                    if (m1_trap) begin
                        m2.next_pc <= {mtvec, 2'b00};
                        m2.cause   <= trap_cause;        // RVFI: the trap's CSR values, as written
                        m2.tval    <= trap_tval;
                        m2.mpie    <= mstatus_mie;
                    end
                end
            end else begin
                m2 <= m2_view;
            end

            // W
            w <= '0;
            if (m2_advance) begin
                w <= m2_view;
                if (m2_view.load) w.result <= load_value(m2_view.rdata, m2_view.addr[1:0], m2_view.funct3);
                else if (m2.mul) w.result <= mul_result;
            end
        end
    end

    // CSRs: written by the CSR instruction (or mret) in M1, at the end of its
    // first cycle there (see the header); by a trap or an interrupt at the
    // commit point (MPIE = MIE, MIE = 0; an exception's mepc is its own PC, an
    // interrupt's the next PC of the instruction completing in M1); and the
    // counters (time_count, read as time/timeh, ticks every cycle and is never
    // written or stopped). A write to a counter replaces that cycle's increment
    // (minstret's is the writing instruction's own). An instruction retires
    // when it passes the commit point without trapping, a serializing one at
    // the end of its first M1 cycle (it cannot be killed after it, and the
    // mcountinhibit it may write applies to the instructions after it only).
    logic m1_first;                 // M1's instruction entered at the last edge
    logic m1_csr_we, retire;
    assign m1_csr_we = m1.valid && m1.csr_we && m1_first;
    assign retire    = m1.valid && (m1.sys ? m1_first : m1_advance && !m1_trap);
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            mstatus_mie  <= 1'b0;
            mstatus_mpie <= 1'b0;
            mie_meie     <= 1'b0;
            mie_mtie     <= 1'b0;
            mie_msie     <= 1'b0;
            cy_inhibit   <= 1'b0;
            ir_inhibit   <= 1'b0;
            mtvec        <= '0;
            mepc         <= '0;
            mscratch     <= '0;
            mcause       <= '0;
            mtval        <= '0;
            mip_q        <= '0;
            mcycle       <= '0;
            time_count   <= '0;
            minstret     <= '0;
            m1_first     <= 1'b0;
        end else begin
            mip_q    <= {meip, mtip, msip};
            m1_first <= e_advance;
            if (kill) begin
                mstatus_mpie <= mstatus_mie;
                mstatus_mie  <= 1'b0;
                mepc         <= m1_trap ? m1.pc[31:2] : m1.next_pc[31:2];
                mcause       <= m1_trap ? {28'b0, trap_cause} : {1'b1, 27'b0, irq_code};
                mtval        <= m1_trap ? trap_tval : 32'b0;
            end else if (m1_csr_we) begin
                if (m1.csr_sel.mstatus) begin
                    mstatus_mie  <= m1.wdata[3];
                    mstatus_mpie <= m1.wdata[7];
                end
                if (m1.csr_sel.mie) begin
                    mie_meie <= m1.wdata[11];
                    mie_mtie <= m1.wdata[7];
                    mie_msie <= m1.wdata[3];
                end
                if (m1.csr_sel.mcountinhibit) begin
                    cy_inhibit <= m1.wdata[0];
                    ir_inhibit <= m1.wdata[2];
                end
                if (m1.csr_sel.mtvec)    mtvec    <= m1.wdata[31:2];    // direct mode
                if (m1.csr_sel.mscratch) mscratch <= m1.wdata;
                if (m1.csr_sel.mepc)     mepc     <= m1.wdata[31:2];
                if (m1.csr_sel.mcause)   mcause   <= m1.wdata;
                if (m1.csr_sel.mtval)    mtval    <= m1.wdata;
            end
            if (m1_csr_we && m1.csr_sel.mcycle)         mcycle[31:0]    <= m1.wdata;
            else if (m1_csr_we && m1.csr_sel.mcycleh)   mcycle[63:32]   <= m1.wdata;
            else if (!cy_inhibit)                       mcycle          <= mcycle + 64'd1;
            time_count <= time_count + 64'd1;
            if (m1_csr_we && m1.csr_sel.minstret)       minstret[31:0]  <= m1.wdata;
            else if (m1_csr_we && m1.csr_sel.minstreth) minstret[63:32] <= m1.wdata;
            else if (retire && !ir_inhibit)             minstret        <= minstret + 64'd1;
        end
    end

    // Write-back and retirement.
    always_ff @(posedge clk) begin
        if (w_write) rf[w.rd] <= w_value;
    end

    logic w_sc, w_amo;
    assign w_sc  = w.insn[6:0] == 7'h2F && w.insn[31:27] == 5'b00011;
    assign w_amo = w.insn[6:0] == 7'h2F && w.insn[31:28] != 4'b0001;    // neither lr nor sc
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            rvfi_valid <= 1'b0;
            rvfi_order <= '0;
        end else begin
            rvfi_valid <= w.valid;
            if (rvfi_valid) rvfi_order <= rvfi_order + 64'd1;
        end
        rvfi_insn      <= w.insn;
        rvfi_trap      <= w.trap;
        rvfi_intr      <= w.intr;
        rvfi_rs1_addr  <= w.rs1;
        rvfi_rs2_addr  <= w.rs2;
        rvfi_rs1_rdata <= w.rs1v;
        rvfi_rs2_rdata <= w.rs2v;
        rvfi_rd_addr   <= w_write ? w.rd : 5'd0;
        rvfi_rd_wdata  <= w_write ? w_value : 32'b0;
        rvfi_pc_rdata  <= w.pc;
        rvfi_pc_wdata  <= w.next_pc;
        // Atomics: an sc reads nothing, and writes only if it succeeded (its
        // answer is 0); an AMO reads the old value and writes the new, which
        // RVFI recomputes here (the memory computed it).
        rvfi_mem_addr  <= (w.load || w.store) ? {w.addr[31:2], 2'b00} : 32'b0;
        rvfi_mem_rmask <= w.load && !w_sc ? w.be : 4'b0;
        rvfi_mem_wmask <= w.store && (!w_sc || w.rdata == 32'b0) ? w.be : 4'b0;
        rvfi_mem_rdata <= w.load && !w_sc ? w.rdata : 32'b0;
        rvfi_mem_wdata <= !w.store ? 32'b0 : w_amo ? amo_value(w.insn[31:27], w.rdata, w.rs2v)
                        : w.wdata & {{8{w.be[3]}}, {8{w.be[2]}}, {8{w.be[1]}}, {8{w.be[0]}}};
    end
    assign rvfi_halt = 1'b0;
    assign rvfi_mode = 2'd3;
    assign rvfi_ixl  = 2'd1;

    // RVFI CSR fields, from W. A CSR instruction reads its CSR (the old value,
    // w.result) and, if it writes, reports the value as it reads back; mret
    // writes mstatus (w.wdata) and mstatush; a trap record writes mepc (its
    // PC), mcause, mtval and mstatus, with the values the trap wrote, carried
    // in its slot (the handler may write them again before the record leaves W
    // when the memory answers late).
    csr_t rd_sel, wr_sel;
    logic w_trap;
    always_comb begin
        rd_sel = w.valid && w.csr_rd ? w.csr_sel : '0;
        wr_sel = w.valid && w.csr_we ? w.csr_sel : '0;
        w_trap = w.valid && w.trap;
    end
    function automatic logic [31:0] mask(input logic on);
        return {32{on}};
    endfunction
    always_ff @(posedge clk) begin
        rvfi_csr_mstatus_rmask       <= mask(rd_sel.mstatus);
        rvfi_csr_mstatus_rdata       <= mask(rd_sel.mstatus) & w.result;
        rvfi_csr_mstatus_wmask       <= mask(wr_sel.mstatus || w_trap);
        rvfi_csr_mstatus_wdata       <= w_trap ? {19'b0, 2'b11, 3'b0, w.mpie, 7'b0}
                                                : mask(wr_sel.mstatus) & (w.wdata & 32'h88 | 32'h1800);
        rvfi_csr_mstatush_rmask      <= mask(rd_sel.mstatush);
        rvfi_csr_mstatush_rdata      <= 32'b0;
        rvfi_csr_mstatush_wmask      <= mask(wr_sel.mstatush || (w.valid && w.mret));
        rvfi_csr_mstatush_wdata      <= 32'b0;
        rvfi_csr_misa_rmask          <= mask(rd_sel.misa);
        rvfi_csr_misa_rdata          <= mask(rd_sel.misa) & MISA;
        rvfi_csr_misa_wmask          <= mask(wr_sel.misa);
        rvfi_csr_misa_wdata          <= mask(wr_sel.misa) & MISA;          // writes are ignored
        rvfi_csr_mie_rmask           <= mask(rd_sel.mie);
        rvfi_csr_mie_rdata           <= mask(rd_sel.mie) & w.result;
        rvfi_csr_mie_wmask           <= mask(wr_sel.mie);
        rvfi_csr_mie_wdata           <= mask(wr_sel.mie) & w.wdata & 32'h888;
        rvfi_csr_mip_rmask           <= mask(rd_sel.mip);
        rvfi_csr_mip_rdata           <= mask(rd_sel.mip) & w.result;
        rvfi_csr_mip_wmask           <= mask(wr_sel.mip);
        rvfi_csr_mip_wdata           <= mask(wr_sel.mip) & w.result;    // no writable bits: reads back as read
        rvfi_csr_mtvec_rmask         <= mask(rd_sel.mtvec);
        rvfi_csr_mtvec_rdata         <= mask(rd_sel.mtvec) & w.result;
        rvfi_csr_mtvec_wmask         <= mask(wr_sel.mtvec);
        rvfi_csr_mtvec_wdata         <= mask(wr_sel.mtvec) & w.wdata & ~32'h3;
        rvfi_csr_mscratch_rmask      <= mask(rd_sel.mscratch);
        rvfi_csr_mscratch_rdata      <= mask(rd_sel.mscratch) & w.result;
        rvfi_csr_mscratch_wmask      <= mask(wr_sel.mscratch);
        rvfi_csr_mscratch_wdata      <= mask(wr_sel.mscratch) & w.wdata;
        rvfi_csr_mepc_rmask          <= mask(rd_sel.mepc);
        rvfi_csr_mepc_rdata          <= mask(rd_sel.mepc) & w.result;
        rvfi_csr_mepc_wmask          <= mask(wr_sel.mepc || w_trap);
        rvfi_csr_mepc_wdata          <= w_trap ? w.pc : mask(wr_sel.mepc) & w.wdata & ~32'h3;
        rvfi_csr_mcause_rmask        <= mask(rd_sel.mcause);
        rvfi_csr_mcause_rdata        <= mask(rd_sel.mcause) & w.result;
        rvfi_csr_mcause_wmask        <= mask(wr_sel.mcause || w_trap);
        rvfi_csr_mcause_wdata        <= w_trap ? {28'b0, w.cause} : mask(wr_sel.mcause) & w.wdata;
        rvfi_csr_mtval_rmask         <= mask(rd_sel.mtval);
        rvfi_csr_mtval_rdata         <= mask(rd_sel.mtval) & w.result;
        rvfi_csr_mtval_wmask         <= mask(wr_sel.mtval || w_trap);
        rvfi_csr_mtval_wdata         <= w_trap ? w.tval : mask(wr_sel.mtval) & w.wdata;
        rvfi_csr_mcountinhibit_rmask <= mask(rd_sel.mcountinhibit);
        rvfi_csr_mcountinhibit_rdata <= mask(rd_sel.mcountinhibit) & w.result;
        rvfi_csr_mcountinhibit_wmask <= mask(wr_sel.mcountinhibit);
        rvfi_csr_mcountinhibit_wdata <= mask(wr_sel.mcountinhibit) & w.wdata & 32'h5;
        rvfi_csr_mcycle_rmask        <= {mask(rd_sel.mcycleh), mask(rd_sel.mcycle)};
        rvfi_csr_mcycle_rdata        <= {mask(rd_sel.mcycleh) & w.result, mask(rd_sel.mcycle) & w.result};
        rvfi_csr_mcycle_wmask        <= {mask(wr_sel.mcycleh), mask(wr_sel.mcycle)};
        rvfi_csr_mcycle_wdata        <= {mask(wr_sel.mcycleh) & w.wdata, mask(wr_sel.mcycle) & w.wdata};
        rvfi_csr_minstret_rmask      <= {mask(rd_sel.minstreth), mask(rd_sel.minstret)};
        rvfi_csr_minstret_rdata      <= {mask(rd_sel.minstreth) & w.result, mask(rd_sel.minstret) & w.result};
        rvfi_csr_minstret_wmask      <= {mask(wr_sel.minstreth), mask(wr_sel.minstret)};
        rvfi_csr_minstret_wdata      <= {mask(wr_sel.minstreth) & w.wdata, mask(wr_sel.minstret) & w.wdata};
    end

    logic unused;
    assign unused = ^{d_raw, f_predecode[1:0], e_redirect_target[1:0], w.acc, w.got, w.funct3,
                     w.mul, w.late, w.dot8, w.sys, rd_sel.zero, rd_sel.mhartid, wr_sel.zero, wr_sel.mhartid,
                     rd_sel.time_lo, rd_sel.time_hi, wr_sel.time_lo, wr_sel.time_hi};

`ifndef SYNTHESIS
    always_ff @(posedge clk) begin
        // The AND-OR operand mux needs exactly one forwarding select, and the
        // AND-OR result exactly one source.
        if (rst_n) assert ($onehot(fsel1) && $onehot(fsel2)) else $error("forwarding select not one-hot");
        if (rst_n && e_valid && !e_err) assert ($onehot(e_dec.res)) else $error("result select not one-hot");
        // The stall logic's inverted copies agree with the selects.
        if (rst_n) assert (fsel1_n == ~fsel1 && fsel2_n == ~fsel2[3:2] && rs1f_lo == rs1f[1:0])
            else $error("inverted select copy differs");
        if (rst_n && d_rsp_valid) assert (rsp_for_m2 || rsp_for_m1) else $error("data answer with no access in flight");
        // A squashed (wrong-path) instruction issues nothing.
        if (rst_n && squash) assert (!d_req_valid && !e_flush && !d_redirect && !div_start)
            else $error("a squashed instruction acted");
        // The divider runs only for a division in Execute, and Execute's
        // division leaves only with its result.
        if (rst_n && div_count != 6'd0) assert (e_live && e_dec.div) else $error("divider running without a division");
        if (rst_n && e_advance && e_dec.div) assert (div_done && div_count == 6'd35) else $error("division left early");
        // A finished division's result stays put while it waits to leave Execute.
        if (rst_n && div_done && $past(div_done) && $past(rst_n))
            assert ($stable(div_result)) else $error("division result changed while held");
        // A multiply never waits in M2 (it has no data access), which the
        // partial products' load enable relies on.
        if (rst_n && m2.valid && m2.mul) assert (m2_advance) else $error("a multiply waits in M2");
        // A CSR write in M1 belongs to an instruction that can no longer be
        // killed: it has no trap and no data access, it entered an empty M1
        // (serializing), and no trap or interrupt is taken while it is there.
        if (rst_n && m1_csr_we) assert (!m1.trap && !m1.acc && m1.sys && !kill && !m1_stop)
            else $error("a CSR write in M1 that could be killed");
        // A serializing instruction never traps (it retires at its first M1 edge).
        if (rst_n && m1.valid && m1.sys) assert (!m1_trap) else $error("a serializing instruction traps");
        if (rst_n && e_advance && e_dec.sys) assert (!m1.valid) else $error("a serializing instruction entered a busy M1");
        // A trap or interrupt and an Execute redirect never coincide (f_target).
        if (rst_n) assert (!(kill && e_flush)) else $error("a trap and an Execute redirect in one cycle");
    end
`endif
endmodule
