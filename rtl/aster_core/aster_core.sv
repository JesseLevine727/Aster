// Aster core: the seven-stage, single-issue, in-order core of docs/cpu.md —
// F1 F2 (aster_core_fetch), Decode, Execute, M1, M2, Write-back.
//
// Milestone 18.1: RV32I (aster_core_pkg). Traps are not yet taken (18.3): an
// instruction with a trap cause — a fetch fault it carries, an illegal
// encoding, a misaligned access or jump target, or a data-port error — stops
// the core at the commit point, the end of M1. It retires as a trap record
// (rvfi_trap), the instructions after it are killed, fetching stops, and
// `trapped` rises in the cycle its record appears on the RVFI port. The
// interrupt inputs and the hart id are unused until 18.3.
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
// - W: load alignment and extension, the register write, and the RVFI record,
//   registered (it appears in the cycle after W).
//
// Load-use: a Decode instruction that reads the result of a load in Execute
// or M1 waits in Decode (2 or 1 cycles); a load's result is forwarded from W.
// A store's data is read like any operand, so it waits the same way.
//
// Data port: Execute presents a request only when M1 can take an instruction
// (M1 is empty or moving on) and M1 holds no trapping instruction, so at most
// two requests are in flight (M1 and M2) and a waiting request stays stable
// until it is accepted: in the cycle after a request waited, M1 is empty.
//
// Verification: `chk_i_redirect` is high in a cycle in which the fetch unit may
// withdraw or replace an unaccepted fetch (a redirect's target presented, or
// fetching stopped); the core never withdraws a data request in 18.1.
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
    // data port (d_req_op: 0 load, 1 store in 18.1)
    output logic        d_req_valid,
    output logic [3:0]  d_req_op,
    output logic [31:0] d_req_addr,
    output logic [31:0] d_req_wdata,
    output logic [3:0]  d_req_be,
    input  logic        d_req_ready,
    input  logic        d_rsp_valid,
    input  logic [31:0] d_rsp_rdata,
    input  logic        d_rsp_error,
    // status and verification
    output logic        trapped,
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
    output logic [31:0] rvfi_mem_wdata
);
    // An instruction in M1, M2 or W.
    typedef struct packed {
        logic        valid;
        logic        trap;
        logic        writes_rd;
        logic        load;
        logic        store;
        logic        acc;           // its data request was accepted
        logic        got;           // its answer has arrived
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
        logic [31:0] wdata;         // store data in its byte lanes
        logic [31:0] rs1v;
        logic [31:0] rs2v;
        logic [31:0] rdata;         // the aligned word read
    } slot_t;

    logic halted;                   // a trap has passed the commit point: no more fetching or issue
    logic squash;                   // an Execute redirect resolved at the last edge: Decode and Execute are wrong-path

    // ------------------------------------------------------------------ fetch
    logic        d_redirect, e_flush, f_valid, f_error, d_take;
    logic [31:2] f_pc;
    logic [31:0] f_insn, d_target, e_next_pc;

    aster_core_fetch #(.RESET_VECTOR(RESET_VECTOR)) fetch (
        .clk, .rst_n,
        .i_req_valid, .i_req_addr, .i_req_ready, .i_rsp_valid, .i_rsp_data, .i_rsp_error,
        .d_redirect, .d_target(d_target[31:2]), .e_flush, .e_target(e_next_pc[31:2]), .halt(halted),
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

    logic d_live;                   // Decode holds an instruction that is not squashed
    assign d_live   = d_valid && !squash;
    assign d_raw    = decode(d_insn);
    assign d_dec    = d_err ? decoded_t'('0) : d_raw;     // a fetch fault carries no operation
    assign d_target = d_pc + d_dec.imm;
    // jal, and a backward branch, redirect from Decode (the target must be aligned).
    assign d_predict  = (d_dec.jal || (d_dec.branch && d_dec.imm[31])) && !d_target[1];
    assign d_redirect = d_live && d_predict && !d_redirected && !halted;

    // --------------------------------------------------------------- Execute
    logic        e_valid, e_err, e_pred;
    logic [31:0] e_pc, e_insn, e_rs1v, e_rs2v;
    decoded_t    e_dec;
    slot_t       m1, m2;
    logic        e_live;             // Execute holds an instruction that is not squashed
    assign e_live = e_valid && !squash;

    // Forwarding from the newest older instruction that writes the register, by
    // the registered selects {M1, M2, W} (computed below, a cycle ahead).
    logic [2:0]  fsel1, fsel2;
    logic [31:0] rs1f, rs2f;
    logic        rs1_ready, rs2_ready;
    function automatic logic [32:0] forward(input logic [2:0] sel, input logic [31:0] held);
        // {ready, value}; a load's value is not ready before W
        if (sel[2]) return {!m1.load, m1.result};
        if (sel[1]) return {!m2.load, m2.result};
        if (sel[0]) return {1'b1, w_value};
        return {1'b1, held};
    endfunction
    assign {rs1_ready, rs1f} = forward(fsel1, e_rs1v);
    assign {rs2_ready, rs2f} = forward(fsel2, e_rs2v);

    logic        e_ready, e_taken, e_misaligned, e_target_misaligned, e_trap, e_access, e_mispredict;
    logic [31:0] e_alu, e_addr, e_link, e_result, e_btarget, e_jtarget, e_wdata;
    logic [1:0]  e_offset;
    logic [3:0]  e_be;
    assign e_ready   = (!e_dec.uses_rs1 || rs1_ready) && (!e_dec.uses_rs2 || rs2_ready);
    assign e_alu     = alu(e_dec.alu_op, e_dec.a_pc ? e_pc : rs1f, e_dec.b_imm ? e_dec.imm : rs2f);
    assign e_link    = e_pc + 32'd4;
    assign e_result  = (e_dec.jal || e_dec.jalr) ? e_link : e_alu;
    assign e_taken   = e_dec.branch && branch_taken(e_dec.funct3, rs1f, rs2f);
    assign e_btarget = e_pc + e_dec.imm;
    assign e_jtarget = {e_alu[31:1], 1'b0};
    assign e_next_pc = e_dec.jalr ? e_jtarget : (e_dec.jal || e_taken) ? e_btarget : e_link;
    assign e_target_misaligned = (e_dec.jalr && e_jtarget[1]) || ((e_dec.jal || e_taken) && e_btarget[1]);
    // A load's or store's address has its own adder, and its alignment comes
    // from the two low bits alone, so the stall logic does not wait for a
    // 32-bit sum. (A fetch fault or an illegal encoding decodes as no access.)
    assign e_addr    = rs1f + e_dec.imm;
    assign e_offset  = rs1f[1:0] + e_dec.imm[1:0];
    assign e_misaligned = (e_dec.load || e_dec.store) &&
                          ((e_dec.funct3[1:0] == 2'd1 && e_offset[0]) || (e_dec.funct3[1:0] == 2'd2 && e_offset != 2'd0));
    assign e_trap    = e_err || e_dec.illegal || e_misaligned || e_target_misaligned;
    assign e_access  = (e_dec.load || e_dec.store) && !e_misaligned;
    assign e_be      = e_dec.funct3[1:0] == 2'd0 ? 4'b0001 << e_offset
                     : e_dec.funct3[1:0] == 2'd1 ? 4'b0011 << e_offset : 4'b1111;
    assign e_wdata   = e_dec.funct3[1:0] == 2'd0 ? {4{rs2f[7:0]}}
                     : e_dec.funct3[1:0] == 2'd1 ? {2{rs2f[15:0]}} : rs2f;
    // A branch to its own fall-through (offset +4) never redirects: its direction
    // does not change the next PC. The trap logic is left out of this path: a
    // branch or jalr that traps (a misaligned target) stops the core at the
    // commit point, which discards its redirect.
    assign e_mispredict = e_dec.jalr || (e_dec.branch && e_taken != e_pred && e_dec.imm != 32'd4);

    // ------------------------------------------------------------ M1, M2, W
    logic d_acc_last;               // a data request was accepted at the last edge (it is in M1)
    logic m1_err_held;
    logic m1_bus_err, m1_trap, kill;
    logic rsp_for_m2, rsp_for_m1;
    slot_t m1_view, m2_view;

    assign m1_bus_err = m1.acc && (d_acc_last ? d_rsp_error : m1_err_held);
    assign m1_trap    = m1.valid && (m1.trap || m1_bus_err);
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
    assign kill       = m1_trap && m1_advance;          // a trap commits at this edge

    assign d_req_valid = e_live && e_access && e_ready && m1_free && !m1_trap && !halted;
    assign d_req_op    = e_dec.store ? 4'd1 : 4'd0;
    assign d_req_addr  = e_addr;
    assign d_req_wdata = e_wdata;
    assign d_req_be    = e_be;

    assign e_advance = e_live && e_ready && m1_free && !m1_trap && (!e_access || d_req_ready);
    assign e_free    = !e_live || e_advance;
    assign e_flush   = e_advance && e_mispredict;

    // Load-use interlock: the newest of Execute and M1 writing the register is a load.
    function automatic logic load_pending(input logic [4:0] r);
        if (r == 5'd0) return 1'b0;
        if (e_live && e_dec.writes_rd && e_dec.rd == r) return e_dec.load;
        return m1.valid && m1.writes_rd && m1.rd == r && m1.load;
    endfunction
    assign d_hazard  = (d_dec.uses_rs1 && load_pending(d_dec.rs1)) || (d_dec.uses_rs2 && load_pending(d_dec.rs2));
    assign d_advance = d_live && e_free && !d_hazard;
    assign d_free    = !d_live || d_advance;
    assign d_take    = d_free && !halted;

    // ------------------------------------------------------------ W values
    assign w_value = w.load ? load_value(w.rdata, w.addr[1:0], w.funct3) : w.result;
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
    function automatic logic [2:0] select(input logic [4:0] r);
        if (r == 5'd0) return 3'b000;
        if (m1n_writes && m1n_rd == r) return 3'b100;
        if (m2n_writes && m2n_rd == r) return 3'b010;
        if (wn_writes && wn_rd == r) return 3'b001;
        return 3'b000;
    endfunction

    // ----------------------------------------------------------- sequential
    slot_t e_slot;
    always_comb begin
        e_slot           = '0;
        e_slot.valid     = 1'b1;
        e_slot.trap      = e_trap;
        e_slot.writes_rd = e_dec.writes_rd && !e_trap;
        e_slot.load      = e_dec.load && !e_trap;
        e_slot.store     = e_dec.store && !e_trap;
        e_slot.acc       = e_access;
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
        e_slot.wdata     = e_wdata;
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            halted       <= 1'b0;
            squash       <= 1'b0;
            fsel1        <= 3'b000;
            fsel2        <= 3'b000;
            wt1          <= 1'b0;
            wt2          <= 1'b0;
            d_valid      <= 1'b0;
            d_redirected <= 1'b0;
            e_valid      <= 1'b0;
            m1           <= '0;
            m2           <= '0;
            w            <= '0;
            d_acc_last   <= 1'b0;
            m1_err_held  <= 1'b0;
        end else begin
            halted     <= halted || kill;
            squash     <= e_flush;
            d_acc_last <= d_req_valid && d_req_ready;
            fsel1      <= select(en_rs1);
            fsel2      <= select(en_rs2);
            wt1        <= wn_writes && wn_rd == dn_rs1;
            wt2        <= wn_writes && wn_rd == dn_rs2;

            // Decode (a squashed instruction leaves: Decode is free)
            if (halted || kill) begin
                d_valid <= 1'b0;
            end else if (d_free) begin
                d_valid      <= f_valid;
                d_pc         <= {f_pc, 2'b00};
                d_insn       <= f_insn;
                d_err        <= f_error;
                d_redirected <= 1'b0;
            end else if (d_redirect) begin
                d_redirected <= 1'b1;
            end

            // Execute (keeps forwarded operands while it waits)
            if (kill) begin
                e_valid <= 1'b0;
            end else if (e_free) begin
                e_valid <= d_advance;
                e_pc    <= d_pc;
                e_insn  <= d_insn;
                e_err   <= d_err;
                e_dec   <= d_dec;
                e_pred  <= d_predict;
                e_rs1v  <= d_rs1v;
                e_rs2v  <= d_rs2v;
            end else begin
                if (rs1_ready) e_rs1v <= rs1f;
                if (rs2_ready) e_rs2v <= rs2f;
            end

            // M1
            if (kill) begin
                m1 <= '0;
            end else if (m1_free) begin
                m1 <= e_advance ? e_slot : '0;
            end else begin
                m1 <= m1_view;
            end
            m1_err_held <= m1_bus_err;

            // M2 (the commit point has passed: a data-port error becomes the trap)
            if (m2_free) begin
                m2 <= '0;
                if (m1_advance) begin
                    m2           <= m1_view;
                    m2.trap      <= m1.trap || m1_bus_err;
                    m2.writes_rd <= m1.writes_rd && !m1_bus_err;
                    m2.load      <= m1.load && !m1_bus_err;
                    m2.store     <= m1.store && !m1_bus_err;
                end
            end else begin
                m2 <= m2_view;
            end

            // W
            w <= m2_advance ? m2_view : '0;
        end
    end

    // Write-back and retirement.
    always_ff @(posedge clk) begin
        if (w_write) rf[w.rd] <= w_value;
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            rvfi_valid <= 1'b0;
            rvfi_order <= '0;
            trapped    <= 1'b0;
        end else begin
            rvfi_valid <= w.valid;
            if (rvfi_valid) rvfi_order <= rvfi_order + 64'd1;
            trapped    <= trapped || (w.valid && w.trap);
        end
        rvfi_insn      <= w.insn;
        rvfi_trap      <= w.trap;
        rvfi_rs1_addr  <= w.rs1;
        rvfi_rs2_addr  <= w.rs2;
        rvfi_rs1_rdata <= w.rs1v;
        rvfi_rs2_rdata <= w.rs2v;
        rvfi_rd_addr   <= w_write ? w.rd : 5'd0;
        rvfi_rd_wdata  <= w_write ? w_value : 32'b0;
        rvfi_pc_rdata  <= w.pc;
        rvfi_pc_wdata  <= w.next_pc;
        rvfi_mem_addr  <= (w.load || w.store) ? {w.addr[31:2], 2'b00} : 32'b0;
        rvfi_mem_rmask <= w.load ? w.be : 4'b0;
        rvfi_mem_wmask <= w.store ? w.be : 4'b0;
        rvfi_mem_rdata <= w.load ? w.rdata : 32'b0;
        rvfi_mem_wdata <= w.store ? w.wdata & {{8{w.be[3]}}, {8{w.be[2]}}, {8{w.be[1]}}, {8{w.be[0]}}} : 32'b0;
    end
    assign rvfi_halt = 1'b0;
    assign rvfi_intr = 1'b0;
    assign rvfi_mode = 2'd3;
    assign rvfi_ixl  = 2'd1;

    logic unused;
    assign unused = ^{meip, mtip, msip, HART_ID, d_raw, d_target[0], w.acc, w.got};

`ifndef SYNTHESIS
    always_ff @(posedge clk) begin
        if (rst_n && d_rsp_valid) assert (rsp_for_m2 || rsp_for_m1) else $error("data answer with no access in flight");
        // A squashed (wrong-path) instruction issues nothing.
        if (rst_n && squash) assert (!d_req_valid && !e_flush && !d_redirect) else $error("a squashed instruction acted");
    end
`endif
endmodule
