// The serial reference fabric (milestone 20.0; docs/soc.md §4): the Phase 20
// fabric's contract built the simplest legal way, as the fabric shell's first
// DUT (19.0 put v1's NPU in the NPU shell the same way). Its ports are the
// Phase 20 fabric's (rtl/fabric, 20.1); its insides are behavioural and are
// not meant for the FPGA.
//
// One memory array, no banks (chk_banks reads 0): any number of reads a
// cycle, and **one write a cycle in all**. Writes are stores, sc and AMOs
// from the data caches, the NPU's writes and the DMA's port W; a failed sc
// still takes the write slot. A read and a write of the same 8-byte unit are
// never accepted together (soc.md §4.2). An AMO takes the write slot when it
// is accepted, and no other write or AMO is accepted until its own write, two
// cycles later; until then its data cache has nothing else accepted (soc.md
// §4.5). Among the requests a cycle allows, priority rotates by one requester
// a cycle (I0, D0, I1, D1, N, R, W), so a request waits a bounded time.
//
// Everything else is soc.md's contract:
// - a request takes effect at the edge that accepts it, an AMO's write two
//   edges later;
// - answers come 2 + WAIT cycles after acceptance, in order per requester;
//   an error (an access outside main memory from I, N, R or W: no effect)
//   comes in the cycle after acceptance, with its answer as usual;
// - a data cache's access outside main memory goes to the I/O bus, one a
//   cycle; the device answers in the next cycle and the fabric returns the
//   answer on the same schedule;
// - reservations (Spike's rule, soc.md §4.5): an lr sets its hart's; another
//   requester's write to any byte of the word, every sc of the hart, its
//   exception and its reset end it. A write ends only the reservation on the
//   word it touches: an lr in the write's cycle reserves its own word, which
//   the write cannot touch (the unit rule); an exception or a reset in an
//   lr's cycle wins over it;
// - snoops: a write's line goes to every other data cache, in the cycle after
//   the write takes effect, on that cache's port for the writer (0 the other
//   data cache, 1 the NPU, 2 the DMA);
// - a hart held in reset has its requests withdrawn and the answers owed to
//   it dropped; an AMO it had accepted still writes.
`timescale 1 ns / 1 ps
module ref_fabric #(
    parameter logic [31:0] MEM_BASE  = 32'h8000_0000,
    parameter int unsigned MEM_BYTES = 96 * 1024,
    parameter int unsigned WAIT      = 0                // cycles added to every answer (simulation)
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic [1:0]            hart_rst_n,           // hart h's caches held in reset
    input  logic [1:0]            hart_exception,       // hart h took an exception (ends its reservation)
    // instruction caches I0, I1
    input  logic [1:0]            i_req_valid,
    input  logic [1:0][29:0]      i_req_addr,           // byte address [31:2]
    output logic [1:0]            i_req_ready,
    output logic [1:0]            i_rsp_valid,
    output logic [1:0][31:0]      i_rsp_data,
    output logic [1:0]            i_rsp_error,
    // data caches D0, D1 (op: aster_core_pkg's OP_*)
    input  logic [1:0]            d_req_valid,
    input  logic [1:0][3:0]       d_req_op,
    input  logic [1:0][31:0]      d_req_addr,
    input  logic [1:0]            d_req_main,     // (aster_fabric's hint; the reference decodes the address)
    input  logic [1:0][31:0]      d_req_wdata,
    input  logic [1:0][3:0]       d_req_be,
    output logic [1:0]            d_req_ready,
    output logic [1:0]            d_rsp_valid,
    output logic [1:0][31:0]      d_rsp_rdata,
    output logic [1:0]            d_rsp_error,
    output logic [1:0][2:0]       snoop_valid,          // [cache][port]
    output logic [1:0][2:0][27:0] snoop_line,           // [31:4]
    // the NPU (N): 8-byte units, writes with byte enables
    input  logic                  n_req_valid,
    input  logic [29:0]           n_req_addr,           // [31:2], bit 2 zero
    input  logic                  n_req_we,
    input  logic [63:0]           n_req_wdata,
    input  logic [7:0]            n_req_be,
    output logic                  n_req_ready,
    output logic                  n_rsp_valid,
    output logic [63:0]           n_rsp_rdata,
    output logic                  n_rsp_error,
    // the DMA's read port R and write port W
    input  logic                  r_req_valid,
    input  logic [28:0]           r_req_addr,           // [31:3]
    output logic                  r_req_ready,
    output logic                  r_rsp_valid,
    output logic [63:0]           r_rsp_rdata,
    output logic                  r_rsp_error,
    input  logic                  w_req_valid,
    input  logic [28:0]           w_req_addr,
    input  logic [63:0]           w_req_wdata,
    input  logic [7:0]            w_req_be,
    output logic                  w_req_ready,
    output logic                  w_rsp_valid,
    output logic                  w_rsp_error,
    // the I/O bus: one access a cycle, always taken; answered in the next cycle
    output logic                  io_req_valid,
    output logic                  io_req_hart,
    output logic [3:0]            io_req_op,
    output logic [31:0]           io_req_addr,
    output logic [31:0]           io_req_wdata,
    output logic [3:0]            io_req_be,
    input  logic [31:0]           io_rsp_rdata,
    output logic [1:0]            ev_resv_end,          // a hart's reservation ended by another requester's write
    // verification
    output logic [2:0]            chk_banks             // 0: no banks (the bank rule is not this design's)
);
    import aster_core_pkg::OP_LOAD, aster_core_pkg::OP_STORE, aster_core_pkg::OP_LR, aster_core_pkg::OP_SC;

    localparam int NREQ = 7, I0 = 0, D0 = 1, I1 = 2, D1 = 3, N = 4, R = 5, W = 6;
    localparam int unsigned UNITS = MEM_BYTES / 8;
    localparam int UB = $clog2(UNITS);
    localparam int L = 2 + WAIT;                       // answer latency

    assign chk_banks = 3'd0;

    logic [63:0] mem [UNITS];
    initial for (int unsigned u = 0; u < UNITS; u++) mem[u] = '0;

    function automatic logic in_mem(input logic [31:0] a);
        return a - MEM_BASE < 32'(MEM_BYTES);
    endfunction
    // The data caches' main-memory hint (aster_fabric's d_req_main, from each cache's registers) must
    // agree with the address the reference decodes.
    always_ff @(posedge clk)
        if (rst_n)
            for (int h = 0; h < 2; h++)
                if (d_req_valid[h] && hart_rst_n[h])
                    assert (d_req_main[h] == in_mem(d_req_addr[h])) else $error("ref_fabric: d_req_main disagrees with the address");
    function automatic logic [4:0] funct5_of(input logic [3:0] op);
        unique case (op)
            4'd5: return 5'b00000; 4'd6: return 5'b00100; 4'd7: return 5'b01100; 4'd8: return 5'b01000;
            4'd9: return 5'b10000; 4'd10: return 5'b10100; 4'd11: return 5'b11000; 4'd12: return 5'b11100;
            default: return 5'b00001;                  // amoswap
        endcase
    endfunction

    // ---- each request, decoded ----
    logic [NREQ-1:0]       valid, tgt_mem, tgt_io, tgt_err, is_write;
    logic [NREQ-1:0][31:0] addr;
    logic [NREQ-1:0][UB-1:0] unit;
    always_comb begin
        addr[I0] = {i_req_addr[0], 2'b00};
        addr[I1] = {i_req_addr[1], 2'b00};
        addr[D0] = d_req_addr[0];
        addr[D1] = d_req_addr[1];
        addr[N]  = {n_req_addr, 2'b00};
        addr[R]  = {r_req_addr, 3'b000};
        addr[W]  = {w_req_addr, 3'b000};
        valid    = {w_req_valid, r_req_valid, n_req_valid, d_req_valid[1] && hart_rst_n[1],
                    i_req_valid[1] && hart_rst_n[1], d_req_valid[0] && hart_rst_n[0], i_req_valid[0] && hart_rst_n[0]};
        for (int k = 0; k < NREQ; k++) begin
            tgt_mem[k] = in_mem(addr[k]);
            tgt_io[k]  = (k == D0 || k == D1) && !tgt_mem[k];
            tgt_err[k] = !tgt_mem[k] && !tgt_io[k];
            unit[k]    = UB'((addr[k] - MEM_BASE) >> 3);
        end
        is_write = '0;
        is_write[D0] = d_req_op[0] != OP_LOAD && d_req_op[0] != OP_LR;
        is_write[D1] = d_req_op[1] != OP_LOAD && d_req_op[1] != OP_LR;
        is_write[N]  = n_req_we;
        is_write[W]  = 1'b1;
    end

    // ---- AMOs in flight: amo1 accepted last cycle, amo2 the cycle before (writes at this edge) ----
    logic          amo1, amo2, amo1_hart, amo2_hart;
    logic [3:0]    amo1_op, amo2_op;
    logic [31:0]   amo1_addr, amo2_addr, amo1_wdata, amo2_wdata;
    logic [1:0]    hold;                                // the data cache waits for its AMO's write
    assign hold = {(amo1 && amo1_hart) || (amo2 && amo2_hart), (amo1 && !amo1_hart) || (amo2 && !amo2_hart)};

    // ---- grants: rotating priority, one write, the unit rule, one I/O access ----
    logic [2:0]          prio;
    logic [NREQ-1:0]     grant;
    always_comb begin
        logic          wr_granted, io_taken, conflict;
        logic [UB-1:0] write_unit, amo_unit;
        int            k;
        conflict = 1'b0;
        grant = '0;
        wr_granted = 1'b0;
        io_taken = 1'b0;
        write_unit = '0;
        amo_unit = UB'((amo2_addr - MEM_BASE) >> 3);
        for (int s = 0; s < NREQ; s++) begin
            k = (int'(prio) + s) % NREQ;
            if (valid[k] && !((k == D0 && hold[0]) || (k == D1 && hold[1]))) begin
                if (tgt_err[k]) grant[k] = 1'b1;
                else if (tgt_io[k]) begin
                    if (!io_taken) begin grant[k] = 1'b1; io_taken = 1'b1; end
                end else if (is_write[k]) begin
                    // one write a cycle, none while an AMO waits for its write, never beside a read of its unit
                    conflict = wr_granted || amo1 || amo2;
                    for (int r = 0; r < NREQ; r++)
                        if (grant[r] && tgt_mem[r] && !is_write[r] && unit[r] == unit[k]) conflict = 1'b1;
                    if (!conflict) begin grant[k] = 1'b1; wr_granted = 1'b1; write_unit = unit[k]; end
                end else if (!(wr_granted && write_unit == unit[k]) && !(amo2 && amo_unit == unit[k]))
                    grant[k] = 1'b1;
            end
        end
    end
    assign i_req_ready = {grant[I1], grant[I0]};
    assign d_req_ready = {grant[D1], grant[D0]};
    assign n_req_ready = grant[N];
    assign r_req_ready = grant[R];
    assign w_req_ready = grant[W];

    // ---- the I/O bus ----
    always_comb begin
        io_req_valid = 1'b0; io_req_hart = 1'b0; io_req_op = '0; io_req_addr = '0; io_req_wdata = '0; io_req_be = '0;
        for (int h = 0; h < 2; h++)
            if (grant[h == 0 ? D0 : D1] && tgt_io[h == 0 ? D0 : D1]) begin
                io_req_valid = 1'b1; io_req_hart = 1'(h); io_req_op = d_req_op[h]; io_req_addr = d_req_addr[h];
                io_req_wdata = d_req_wdata[h]; io_req_be = d_req_be[h];
            end
    end

    // ---- answers: a pipeline of L stages per requester ----
    logic [NREQ-1:0][L-1:0]        pipe_v, pipe_io;
    logic [NREQ-1:0][L-1:0][63:0]  pipe_d;
    logic [NREQ-1:0]               err_q;
    assign i_rsp_valid = {pipe_v[I1][L-1], pipe_v[I0][L-1]};
    assign i_rsp_data  = {pipe_d[I1][L-1][31:0], pipe_d[I0][L-1][31:0]};
    assign i_rsp_error = {err_q[I1], err_q[I0]};
    assign d_rsp_valid = {pipe_v[D1][L-1], pipe_v[D0][L-1]};
    assign d_rsp_rdata = {pipe_d[D1][L-1][31:0], pipe_d[D0][L-1][31:0]};
    assign d_rsp_error = {err_q[D1], err_q[D0]};
    assign n_rsp_valid = pipe_v[N][L-1];
    assign n_rsp_rdata = pipe_d[N][L-1];
    assign n_rsp_error = err_q[N];
    assign r_rsp_valid = pipe_v[R][L-1];
    assign r_rsp_rdata = pipe_d[R][L-1];
    assign r_rsp_error = err_q[R];
    assign w_rsp_valid = pipe_v[W][L-1];
    assign w_rsp_error = err_q[W];

    // ---- reservations ----
    logic [1:0]       resv;
    logic [1:0][29:0] resv_word;

    // A write's word touches: its byte enables over the unit's two words.
    function automatic logic touches(input logic [28:0] wunit, input logic [7:0] be8, input logic [29:0] word);
        return (wunit == word[29:1]) && (word[0] ? |be8[7:4] : |be8[3:0]);
    endfunction

    /* verilator lint_off UNUSEDSIGNAL */
    always_ff @(posedge clk) begin
        logic [63:0] old, wide;
        logic [31:0] word_old, word_new;
        logic [7:0]  be8;
        logic        sc_ok, wrote;
        logic [31:0] waddr;                            // its address ([2:0] unused: the unit's bytes are wbe)
        logic [7:0]  wbe;
        int          writer;                           // the requester whose write takes effect at this edge
        if (!rst_n) begin
            pipe_v <= '0; err_q <= '0; amo1 <= 1'b0; amo2 <= 1'b0; resv <= '0; snoop_valid <= '0; prio <= '0;
            ev_resv_end <= '0;
        end else begin
            prio <= prio == 3'(NREQ - 1) ? 3'd0 : prio + 3'd1;
            // answers advance
            for (int k = 0; k < NREQ; k++) begin
                for (int s = L - 1; s > 0; s--) begin
                    pipe_v[k][s]  <= pipe_v[k][s-1];
                    pipe_io[k][s] <= 1'b0;
                    pipe_d[k][s]  <= pipe_io[k][s-1] ? 64'(io_rsp_rdata) : pipe_d[k][s-1];
                end
                pipe_v[k][0]  <= grant[k];
                pipe_io[k][0] <= grant[k] && tgt_io[k];
                pipe_d[k][0]  <= '0;
                err_q[k]      <= grant[k] && tgt_err[k];
            end
            // the effect of each accepted request
            wrote = 1'b0; writer = 0; waddr = '0; wbe = '0;
            for (int k = 0; k < NREQ; k++)
                if (grant[k] && tgt_mem[k]) begin
                    old = mem[unit[k]];
                    if (k == D0 || k == D1) begin
                        automatic int h = k == D0 ? 0 : 1;
                        be8 = addr[k][2] ? {d_req_be[h], 4'h0} : {4'h0, d_req_be[h]};
                        wide = {d_req_wdata[h], d_req_wdata[h]};
                        word_old = addr[k][2] ? old[63:32] : old[31:0];
                        unique case (d_req_op[h])
                            OP_LOAD:  pipe_d[k][0] <= 64'(word_old);
                            OP_LR:    pipe_d[k][0] <= 64'(word_old);   // its reservation below
                            OP_STORE: begin
                                for (int b = 0; b < 8; b++) if (be8[b]) mem[unit[k]][8*b +: 8] <= wide[8*b +: 8];
                                wrote = 1'b1; writer = k; waddr = addr[k]; wbe = be8;
                            end
                            OP_SC: begin
                                sc_ok = resv[h] && resv_word[h] == addr[k][31:2];
                                pipe_d[k][0] <= 64'({31'b0, !sc_ok});
                                if (sc_ok) begin
                                    for (int b = 0; b < 8; b++) if (be8[b]) mem[unit[k]][8*b +: 8] <= wide[8*b +: 8];
                                    wrote = 1'b1; writer = k; waddr = addr[k]; wbe = be8;
                                end
                            end
                            default: begin                 // an AMO: its old value now, its write in two edges
                                pipe_d[k][0] <= 64'(word_old);
                            end
                        endcase
                    end else if (k == N && n_req_we) begin
                        for (int b = 0; b < 8; b++) if (n_req_be[b]) mem[unit[k]][8*b +: 8] <= n_req_wdata[8*b +: 8];
                        wrote = 1'b1; writer = k; waddr = addr[k]; wbe = n_req_be;
                    end else if (k == W) begin
                        for (int b = 0; b < 8; b++) if (w_req_be[b]) mem[unit[k]][8*b +: 8] <= w_req_wdata[8*b +: 8];
                        wrote = 1'b1; writer = k; waddr = addr[k]; wbe = w_req_be;
                    end else pipe_d[k][0] <= old;          // I (its word), N and R reads
                    if (k == I0 || k == I1) pipe_d[k][0] <= {32'b0, addr[k][2] ? old[63:32] : old[31:0]};
                end
            // AMOs
            amo1 <= 1'b0;
            for (int h = 0; h < 2; h++) begin
                if (grant[h == 0 ? D0 : D1] && tgt_mem[h == 0 ? D0 : D1] && d_req_op[h] > OP_SC) begin
                    amo1 <= 1'b1; amo1_hart <= 1'(h); amo1_op <= d_req_op[h]; amo1_addr <= d_req_addr[h];
                    amo1_wdata <= d_req_wdata[h];
                end
            end
            amo2 <= amo1; amo2_hart <= amo1_hart; amo2_op <= amo1_op; amo2_addr <= amo1_addr; amo2_wdata <= amo1_wdata;
            if (amo2) begin                                 // the AMO's write (no other write in this cycle)
                old = mem[UB'((amo2_addr - MEM_BASE) >> 3)];
                word_old = amo2_addr[2] ? old[63:32] : old[31:0];
                word_new = aster_core_pkg::amo_value(funct5_of(amo2_op), word_old, amo2_wdata);
                if (amo2_addr[2]) mem[UB'((amo2_addr - MEM_BASE) >> 3)][63:32] <= word_new;
                else mem[UB'((amo2_addr - MEM_BASE) >> 3)][31:0] <= word_new;
                wrote = 1'b1; writer = amo2_hart ? D1 : D0; waddr = amo2_addr; wbe = amo2_addr[2] ? 8'hF0 : 8'h0F;
            end
            // snoops and reservations for the write that takes effect at this edge
            snoop_valid <= '0;
            if (wrote)
                for (int c = 0; c < 2; c++)
                    if (writer != (c == 0 ? D0 : D1)) begin
                        automatic int port = writer == N ? 1 : writer == W ? 2 : 0;
                        snoop_valid[c][port] <= 1'b1;
                        snoop_line[c][port]  <= waddr[31:4];
                    end
            for (int h = 0; h < 2; h++) begin
                automatic logic        mine = grant[h == 0 ? D0 : D1] && tgt_mem[h == 0 ? D0 : D1];
                automatic logic        lr_now = mine && d_req_op[h] == OP_LR;
                automatic logic [29:0] word = lr_now ? d_req_addr[h][31:2] : resv_word[h];
                automatic logic        keep = lr_now || resv[h];
                if (mine && d_req_op[h] == OP_SC) keep = 1'b0;
                ev_resv_end[h] <= keep && wrote && writer != (h == 0 ? D0 : D1) && touches(waddr[31:3], wbe, word);
                if (wrote && writer != (h == 0 ? D0 : D1) && touches(waddr[31:3], wbe, word)) keep = 1'b0;
                if (hart_exception[h] || !hart_rst_n[h]) keep = 1'b0;
                resv[h] <= keep;
                resv_word[h] <= word;
            end
            // a hart held in reset: the answers owed to it are dropped
            for (int h = 0; h < 2; h++)
                if (!hart_rst_n[h]) begin
                    pipe_v[h == 0 ? I0 : I1] <= '0; pipe_v[h == 0 ? D0 : D1] <= '0;
                    err_q[h == 0 ? I0 : I1] <= 1'b0; err_q[h == 0 ? D0 : D1] <= 1'b0;
                end
        end
    end
    /* verilator lint_on UNUSEDSIGNAL */
endmodule
