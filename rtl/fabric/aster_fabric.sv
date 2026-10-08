// The Phase 20 memory fabric (milestone 20.1; docs/soc.md §4): seven
// requesters — the instruction caches I0 and I1, the data caches D0 and D1,
// the NPU's port N, the DMA's ports R and W — on 96 KiB of main memory in
// four banks, and the data caches' I/O bus. Its ports are the serial
// reference fabric's (verification/fabric/ref_fabric.sv), and it keeps the
// same contract; the fabric shell checks it cycle by cycle.
//
// Banks (soc.md §4.2): bank = address bits [5:4] (a 16-byte line in one bank),
// each 3,072 × 64 bits of block RAM answering two cycles after acceptance.
// Port A takes the bank's write, if any, or a read; port B only reads. The
// bank rule holds: at most two accesses a cycle, at most one a write, never a
// read and a write of one 8-byte unit.
//
// Arbitration (soc.md §4.4, as clarified in 20.1): each port of each bank has
// its own round-robin arbiter over a fixed group of requesters, the two
// working in parallel —
// - port A: the data caches (their reads, writes and atomics), the DMA's
//   writes (W) and the NPU's writes;
// - port B: the instruction caches, the DMA's reads (R) and the NPU's reads.
// Each group has four members — port A: D0, D1, N, W; port B: I0, I1, R, N
// (the NPU is in one or the other by its op) — so a grant is a function of
// four eligibilities and a two-bit pointer, one LUT. Port B's pick waits when
// port A's pick writes the same unit (a store, an sc or an AMO's acceptance);
// then in the next cycle port A takes no write to that unit, so the held-back
// read goes through (no requester waits forever, soc.md §4.4). (20.1's second
// chance, a data cache's load on an idle port B, was removed by the owner's
// decision in 20.2: it cost about 2 ns at 10 ns and at most 0.03% of the
// cycles measured; soc.md §13, 20.2.) A hart alone is never slowed:
// its data cache is on port A and its instruction cache on port B. Each
// pointer moves past the requester its port took. An AMO's new value is
// computed in its bank, from that bank's port A output, for both halves of
// the unit at once (the byte enables write only the AMO's), so neither a bank
// nor a half multiplexer lies on the way back in. A request is eligible on its port when it is for
// main memory in that bank and
// - port A: it is not from a data cache waiting for its AMO's write; it is
//   not a write while an AMO in its bank waits for its write; and the bank's
//   port A is not taken by an AMO's write this cycle;
// - port B: it is not of the unit an AMO writes this cycle.
// Requests outside main memory from I, N, R and W are taken at once and
// answered with an error, with no effect. A data cache's other requests go to
// the I/O bus, one a cycle, D0 and D1 in turn.
//
// Answers (soc.md §4.3): two plus WAIT cycles after acceptance, in order; the
// error bit in the cycle after acceptance. Atomics (§4.5): an AMO reads at
// acceptance (port A, its answer the old value) and its unit (one a hart)
// writes the new value through port A at the edge two cycles later. lr sets
// its hart's reservation; another requester's write to any byte of the word,
// every sc of the hart, its exception and its reset end it (Spike's rule); an
// sc writes only when its hart holds the reservation on its word. Snoops
// (§4.6): each write's line goes to every other data cache in the cycle after
// it takes effect, on that cache's port for the writer (0 the other data
// cache, 1 the NPU, 2 the DMA's W). A hart held in reset has its requests
// ignored and the answers owed to it dropped; an AMO it had accepted still
// writes.
//
// Every request comes from its requester's registers, and readiness is
// decided in the cycle (acceptance is the access's place in the memory
// order). The snoops and the reservations are computed from each writer's
// grant and terms of its request alone, so they add little after the
// arbiters.
`timescale 1 ns / 1 ps
module aster_fabric #(
    parameter logic [31:0] MEM_BASE  = 32'h8000_0000,
    parameter int unsigned MEM_BYTES = 96 * 1024,
    parameter int unsigned WAIT      = 0                // cycles added to every answer (simulation)
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic [1:0]            hart_rst_n,
    input  logic [1:0]            hart_exception,
    input  logic [1:0]            i_req_valid,
    input  logic [1:0][29:0]      i_req_addr,
    input  logic [1:0]            i_req_main,     // each requester's request is main memory's (else an error, or a data
                                                  // cache's I/O access): its registered decision, not decoded here
    output logic [1:0]            i_req_ready,
    output logic [1:0]            i_rsp_valid,
    output logic [1:0][31:0]      i_rsp_data,
    output logic [1:0]            i_rsp_error,
    input  logic [1:0]            d_req_valid,
    input  logic [1:0][3:0]       d_req_op,
    input  logic [1:0][31:0]      d_req_addr,
    input  logic [1:0]            d_req_main,     // the request is main memory's (the cache's registered decision; else I/O)
    input  logic [1:0][31:0]      d_req_wdata,
    input  logic [1:0][3:0]       d_req_be,
    output logic [1:0]            d_req_ready,
    output logic [1:0]            d_rsp_valid,
    output logic [1:0][31:0]      d_rsp_rdata,
    output logic [1:0]            d_rsp_error,
    output logic [1:0][2:0]       snoop_valid,
    output logic [1:0][2:0][27:0] snoop_line,
    input  logic                  n_req_valid,
    input  logic [29:0]           n_req_addr,
    input  logic                  n_req_main,
    input  logic                  n_req_we,
    input  logic [63:0]           n_req_wdata,
    input  logic [7:0]            n_req_be,
    output logic                  n_req_ready,
    output logic                  n_rsp_valid,
    output logic [63:0]           n_rsp_rdata,
    output logic                  n_rsp_error,
    input  logic                  r_req_valid,
    input  logic [28:0]           r_req_addr,
    input  logic                  r_req_main,
    output logic                  r_req_ready,
    output logic                  r_rsp_valid,
    output logic [63:0]           r_rsp_rdata,
    output logic                  r_rsp_error,
    input  logic                  w_req_valid,
    input  logic [28:0]           w_req_addr,
    input  logic                  w_req_main,
    input  logic [63:0]           w_req_wdata,
    input  logic [7:0]            w_req_be,
    output logic                  w_req_ready,
    output logic                  w_rsp_valid,
    output logic                  w_rsp_error,
    output logic                  io_req_valid,
    output logic                  io_req_hart,
    output logic [3:0]            io_req_op,
    output logic [31:0]           io_req_addr,
    output logic [31:0]           io_req_wdata,
    output logic [3:0]            io_req_be,
    input  logic [31:0]           io_rsp_rdata,
    output logic [1:0]            ev_resv_end,          // a hart's reservation ended by another requester's write
    output logic [2:0]            chk_banks
);
    import aster_core_pkg::OP_LOAD, aster_core_pkg::OP_STORE, aster_core_pkg::OP_LR, aster_core_pkg::OP_SC;

    localparam int NREQ = 7, I0 = 0, D0 = 1, I1 = 2, D1 = 3, N = 4, R = 5, W = 6;
    localparam int BANKS = 4;
    localparam int unsigned DEPTH = MEM_BYTES / 8 / BANKS;   // units a bank
    localparam int IB = $clog2(DEPTH);                       // a bank's index bits
    localparam int UB = $clog2(MEM_BYTES / 8);               // a unit's bits in main memory
    localparam int AB = $clog2(MEM_BYTES);                   // main memory's offset bits (MEM_BASE aligned to 2^AB)

    assign chk_banks = 3'(BANKS);

    function automatic logic [4:0] funct5_of(input logic [3:0] op);
        unique case (op)
            4'd5: return 5'b00000; 4'd6: return 5'b00100; 4'd7: return 5'b01100; 4'd8: return 5'b01000;
            4'd9: return 5'b10000; 4'd10: return 5'b10100; 4'd11: return 5'b11000; 4'd12: return 5'b11100;
            default: return 5'b00001;
        endcase
    endfunction
    // Round robin over a group of four: the first eligible member at or after the pointer. The
    // member's index is an unsigned 2-bit sum: a size cast of an int keeps its sign (IEEE 1800), so
    // 2'(int) indexes 2 and 3 as -2 and -1, which Vivado synthesizes as out of range — members 2 and 3
    // (the NPU and the DMA) were never granted in 20.1's netlists, which Verilator does not show.
    function automatic logic [3:0] rr4(input logic [3:0] elig, input logic [1:0] ptr);
        logic [3:0] g;
        logic [1:0] at;
        g = '0;
        for (int s = 0; s < 4; s++) begin
            at = ptr + s[1:0];
            if (g == '0 && elig[at]) g[at] = 1'b1;
        end
        return g;
    endfunction
    function automatic logic [1:0] after(input logic [3:0] g);   // the pointer past a one-hot grant
        unique case (g)
            4'b0001: return 2'd1;
            4'b0010: return 2'd2;
            4'b0100: return 2'd3;
            default: return 2'd0;                                 // 4'b1000
        endcase
    endfunction
    // The groups: port A's members and port B's, in round-robin order.
    localparam int GA0 = 1, GA1 = 3, GA2 = 4, GA3 = 6;        // D0, D1, N, W
    localparam int GB0 = 0, GB1 = 2, GB2 = 5, GB3 = 4;        // I0, I1, R, N
    // The groups as arrays.
    localparam int ga[4] = '{GA0, GA1, GA2, GA3};
    localparam int gb[4] = '{GB0, GB1, GB2, GB3};

    // ---- each request, decoded ----
    logic [NREQ-1:0]          valid, tgt_mem, tgt_io, tgt_err, wclass, is_amo, on_port_a, eff_write, main_hint;
    logic [NREQ-1:0][31:0]    addr;
    logic [NREQ-1:0][UB-1:0]  unit;
    logic [NREQ-1:0][1:0]     bank;
    logic [NREQ-1:0][IB-1:0]  index;
    logic [NREQ-1:0][63:0]    wdata;
    logic [NREQ-1:0][7:0]     be8;
    always_comb begin
        addr[I0] = {i_req_addr[0], 2'b00};
        addr[I1] = {i_req_addr[1], 2'b00};
        addr[D0] = d_req_addr[0];
        addr[D1] = d_req_addr[1];
        addr[N]  = {n_req_addr, 2'b00};
        addr[R]  = {r_req_addr, 3'b000};
        addr[W]  = {w_req_addr, 3'b000};
        main_hint = {w_req_main, r_req_main, n_req_main, d_req_main[1], i_req_main[1], d_req_main[0], i_req_main[0]};
        valid = {w_req_valid, r_req_valid, n_req_valid, d_req_valid[1] && hart_rst_n[1],
                 i_req_valid[1] && hart_rst_n[1], d_req_valid[0] && hart_rst_n[0], i_req_valid[0] && hart_rst_n[0]};
        wclass = '0;
        is_amo = '0;
        for (int h = 0; h < 2; h++) begin
            wclass[h == 0 ? D0 : D1] = d_req_op[h] != OP_LOAD && d_req_op[h] != OP_LR;
            is_amo[h == 0 ? D0 : D1] = d_req_op[h] > OP_SC;
        end
        wclass[N] = n_req_we;
        wclass[W] = 1'b1;
        on_port_a = '0;
        on_port_a[D0] = 1'b1; on_port_a[D1] = 1'b1; on_port_a[W] = 1'b1; on_port_a[N] = n_req_we;
        for (int k = 0; k < NREQ; k++) begin
            // the target from the requester's own registers, not decoded again from the address (20.2's timing)
            tgt_mem[k] = main_hint[k];
            tgt_io[k]  = (k == D0 || k == D1) && !tgt_mem[k];
            tgt_err[k] = !tgt_mem[k] && !tgt_io[k];
            unit[k]    = addr[k][UB+2:3];
            bank[k]    = addr[k][5:4];
            index[k]   = IB'({unit[k][UB-1:3], unit[k][0]});
        end
        wdata[I0] = '0; wdata[I1] = '0; wdata[R] = '0;
        be8[I0] = '0; be8[I1] = '0; be8[R] = '0;
        for (int h = 0; h < 2; h++) begin
            wdata[h == 0 ? D0 : D1] = {d_req_wdata[h], d_req_wdata[h]};
            be8[h == 0 ? D0 : D1]   = d_req_addr[h][2] ? {d_req_be[h], 4'h0} : {4'h0, d_req_be[h]};
        end
        wdata[N] = n_req_wdata; be8[N] = n_req_be;
        wdata[W] = w_req_wdata; be8[W] = w_req_be;
    end

    // ---- AMOs in flight, a hart each: stage 1 accepted last cycle, stage 2 writes at this edge ----
    logic [1:0]          amo1, amo2;
    logic [1:0][3:0]     amo1_op;
    logic [1:0][31:0]    amo1_addr, amo2_addr, amo1_operand;
    logic [1:0]          hold;                       // the data cache waits for its AMO's write
    logic [BANKS-1:0]    amo_busy, amo_wr_now;       // an AMO in the bank waits for its write; writes now (a register)
    // Each bank's AMO writing now, loaded the cycle before from its hart's stage 1, so the
    // write cycle's path starts at registers: whether one writes, its word's half, its op,
    // operand and index.
    logic [BANKS-1:0]           amo_hi;
    logic [BANKS-1:0][4:0]      amo_f5;
    logic [BANKS-1:0][31:0]     amo_operand_b;
    logic [BANKS-1:0][IB-1:0]   amo_index;
    always_comb begin
        hold = amo1 | amo2;
        amo_busy = amo_wr_now;
        for (int h = 0; h < 2; h++) if (amo1[h]) amo_busy[amo1_addr[h][5:4]] = 1'b1;
    end

    // ---- reservations, and whether each sc writes ----
    // A reservation is set only by an lr a bank takes, so it holds a word of main memory; and an sc's
    // success (like a write touching the word) matters only for a request of main memory, whose address
    // main memory's offset bits distinguish (main_hint agrees with the address: asserted below). So the
    // reserved word is kept, and compared, as its offset in main memory (20.3's timing: 15 bits, not 30).
    logic [1:0]           resv;
    logic [1:0][AB-3:0]   resv_word;
    logic [NREQ-1:0]      sc_ok;
    always_comb begin
        sc_ok = '0;
        for (int h = 0; h < 2; h++)
            sc_ok[h == 0 ? D0 : D1] = resv[h] && resv_word[h] == d_req_addr[h][AB-1:2];
        // what each request writes if it is taken: a store, a successful sc, an NPU or DMA write
        eff_write = '0;
        for (int h = 0; h < 2; h++)
            eff_write[h == 0 ? D0 : D1] = d_req_op[h] == OP_STORE || (d_req_op[h] == OP_SC && sc_ok[h == 0 ? D0 : D1]);
        eff_write[N] = n_req_we;
        eff_write[W] = 1'b1;
    end

    // ---- the arbiters: port A and port B of each bank, in parallel ----
    // A write on port A holds back a read of its unit on port B (precomputed pairs). Every unit compared
    // here is of one bank (two picks of a bank; a request of bank b and a unit of bank b held for it), so
    // the units' bank bits agree and the compare takes their indices (20.2's timing: 12 bits, not 14).
    logic [3:0][3:0]            blocks;              // blocks[i][j]: port A's member i writes port B's member j's unit
    logic [BANKS-1:0][1:0]      ptr_a, ptr_b;        // the round-robin pointers
    logic [BANKS-1:0][3:0]      elig_a, elig_b, pick_a, pick_b, take_b;
    logic [BANKS-1:0]           blk_v;               // port B's pick was held back last cycle ...
    logic [BANKS-1:0][IB-1:0]   blk_index;           // ... by a write of this unit, which port A now refuses
    logic [NREQ-1:0]            grant, io_grant;
    logic                       io_ptr;              // the data cache the I/O bus favours (0: D0)
    always_comb begin
        int k;
        for (int i = 0; i < 4; i++) begin
            for (int j = 0; j < 4; j++) blocks[i][j] = wclass[ga[i]] && index[ga[i]] == index[gb[j]];
        end
        grant = '0;
        for (int b = 0; b < BANKS; b++) begin
            for (int i = 0; i < 4; i++) begin
                k = ga[i];
                elig_a[b][i] = valid[k] && on_port_a[k] && tgt_mem[k] && bank[k] == 2'(b) && !amo_wr_now[b]
                               && !((k == D0 && hold[0]) || (k == D1 && hold[1])) && !(wclass[k] && amo_busy[b])
                               && !(blk_v[b] && wclass[k] && index[k] == blk_index[b]);
                k = gb[i];
                elig_b[b][i] = valid[k] && !on_port_a[k] && tgt_mem[k] && bank[k] == 2'(b)
                               && !(amo_wr_now[b] && index[k] == amo_index[b]);
            end
            pick_a[b] = rr4(elig_a[b], ptr_a[b]);
            pick_b[b] = rr4(elig_b[b], ptr_b[b]);
            for (int j = 0; j < 4; j++) begin
                logic blocked;
                blocked = 1'b0;
                for (int i = 0; i < 4; i++) if (pick_a[b][i] && blocks[i][j]) blocked = 1'b1;
                take_b[b][j] = pick_b[b][j] && !blocked;
            end
            for (int i = 0; i < 4; i++) begin
                if (pick_a[b][i]) grant[ga[i]] = 1'b1;
                if (take_b[b][i]) grant[gb[i]] = 1'b1;
            end
        end
        // the I/O bus: one access a cycle, D0 and D1 in turn; a data cache waiting for its AMO's write waits
        io_grant = '0;
        if (valid[D0] && tgt_io[D0] && !hold[0] && (!io_ptr || !(valid[D1] && tgt_io[D1] && !hold[1]))) io_grant[D0] = 1'b1;
        else if (valid[D1] && tgt_io[D1] && !hold[1]) io_grant[D1] = 1'b1;
        grant = grant | io_grant | (valid & tgt_err);
    end
    assign i_req_ready = {grant[I1], grant[I0]};
    assign d_req_ready = {grant[D1], grant[D0]};
    assign n_req_ready = grant[N];
    assign r_req_ready = grant[R];
    assign w_req_ready = grant[W];

    always_comb begin
        io_req_valid = 1'b0; io_req_hart = 1'b0; io_req_op = '0; io_req_addr = '0; io_req_wdata = '0; io_req_be = '0;
        for (int h = 0; h < 2; h++)
            if (io_grant[h == 0 ? D0 : D1]) begin
                io_req_valid = 1'b1; io_req_hart = 1'(h); io_req_op = d_req_op[h]; io_req_addr = d_req_addr[h];
                io_req_wdata = d_req_wdata[h]; io_req_be = d_req_be[h];
            end
    end

    // ---- the banks' ports: A the write (or a read, or an AMO's write), B a read ----
    logic [BANKS-1:0]           en_a, en_b;
    logic [BANKS-1:0][7:0]      we_a;
    logic [BANKS-1:0][IB-1:0]   addr_a, addr_b;
    logic [BANKS-1:0][63:0]     din_a, q_a, q_b;
    logic [BANKS-1:0][63:0]     amo_new;             // the AMO's new value for each half of the unit
    always_comb begin
        for (int b = 0; b < BANKS; b++) begin
            amo_new[b] = {aster_core_pkg::amo_value(amo_f5[b], q_a[b][63:32], amo_operand_b[b]),
                          aster_core_pkg::amo_value(amo_f5[b], q_a[b][31:0], amo_operand_b[b])};
            en_a[b] = |pick_a[b] || amo_wr_now[b];
            en_b[b] = |take_b[b];
            addr_a[b] = '0; addr_b[b] = '0; din_a[b] = '0; we_a[b] = '0;
            for (int i = 0; i < 4; i++) begin
                if (pick_a[b][i]) begin
                    addr_a[b] = index[ga[i]];
                    din_a[b]  = wdata[ga[i]];
                    we_a[b]   = eff_write[ga[i]] ? be8[ga[i]] : 8'h00;    // an AMO only reads now
                end
                if (pick_b[b][i]) addr_b[b] = index[gb[i]];
            end
            if (amo_wr_now[b]) begin
                addr_a[b] = amo_index[b];
                din_a[b]  = amo_new[b];
                we_a[b]   = amo_hi[b] ? 8'hF0 : 8'h0F;
            end
        end
    end

    for (genvar b = 0; b < BANKS; b++) begin : g_bank
        aster_fabric_bank #(.DEPTH(DEPTH)) mem (
            .clk, .en_a(en_a[b]), .we_a(we_a[b]), .addr_a(addr_a[b]), .din_a(din_a[b]), .q_a(q_a[b]),
            .en_b(en_b[b]), .addr_b(addr_b[b]), .q_b(q_b[b])
        );
    end

    // ---- answers (a requester's port is fixed by its group, the NPU's by its op) ----
    typedef enum logic [1:0] {K_MEM, K_IO, K_SC, K_NONE} kind_t;
    logic [NREQ-1:0]       v1, v2, err_q, half1, half2, port1, port2, scfail1, scfail2;
    kind_t [NREQ-1:0]      kind1, kind2;
    logic [NREQ-1:0][1:0]  bank1, bank2;
    logic [NREQ-1:0][31:0] io2;
    logic [NREQ-1:0][63:0] data2;
    always_comb
        for (int k = 0; k < NREQ; k++) begin
            logic [63:0] unit_data;
            unit_data = port2[k] ? q_b[bank2[k]] : q_a[bank2[k]];
            unique case (kind2[k])
                K_MEM:   data2[k] = (k == N || k == R) ? unit_data : {32'b0, half2[k] ? unit_data[63:32] : unit_data[31:0]};
                K_IO:    data2[k] = 64'(io2[k]);
                K_SC:    data2[k] = 64'(scfail2[k]);
                default: data2[k] = '0;
            endcase
        end
    logic [NREQ-1:0] rsp_v;
    logic [NREQ-1:0][63:0] rsp_d;
    if (WAIT == 0) begin : g_now
        assign rsp_v = v2;
        assign rsp_d = data2;
    end else begin : g_wait
        logic [WAIT-1:0][NREQ-1:0]       wv;
        logic [WAIT-1:0][NREQ-1:0][63:0] wd;
        always_ff @(posedge clk) begin
            wv[0] <= v2; wd[0] <= data2;
            for (int s = 1; s < WAIT; s++) begin wv[s] <= wv[s-1]; wd[s] <= wd[s-1]; end
            if (!rst_n) wv <= '0;
            for (int h = 0; h < 2; h++)
                if (!hart_rst_n[h])
                    for (int s = 0; s < WAIT; s++) begin wv[s][h == 0 ? I0 : I1] <= 1'b0; wv[s][h == 0 ? D0 : D1] <= 1'b0; end
        end
        assign rsp_v = wv[WAIT-1];
        assign rsp_d = wd[WAIT-1];
    end
    assign i_rsp_valid = {rsp_v[I1], rsp_v[I0]};
    assign i_rsp_data  = {rsp_d[I1][31:0], rsp_d[I0][31:0]};
    assign i_rsp_error = {err_q[I1], err_q[I0]};
    assign d_rsp_valid = {rsp_v[D1], rsp_v[D0]};
    assign d_rsp_rdata = {rsp_d[D1][31:0], rsp_d[D0][31:0]};
    assign d_rsp_error = {err_q[D1], err_q[D0]};
    assign n_rsp_valid = rsp_v[N];
    assign n_rsp_rdata = rsp_d[N];
    assign n_rsp_error = err_q[N];
    assign r_rsp_valid = rsp_v[R];
    assign r_rsp_rdata = rsp_d[R];
    assign r_rsp_error = err_q[R];
    assign w_rsp_valid = rsp_v[W];
    assign w_rsp_error = err_q[W];

    // ---- the writes that take effect at this edge, one a writer: D0, D1 (a store, an sc
    // or its AMO's write), N, W — from each writer's grant and its own request ----
    logic [3:0]        wrote;                        // D0, D1, N, W
    logic [3:0][31:0]  wr_addr;
    logic [3:0][7:0]   wr_be;
    always_comb begin
        for (int h = 0; h < 2; h++) begin
            wrote[h]   = (grant[h == 0 ? D0 : D1] && tgt_mem[h == 0 ? D0 : D1] && eff_write[h == 0 ? D0 : D1]) || amo2[h];
            wr_addr[h] = amo2[h] ? amo2_addr[h] : d_req_addr[h];
            wr_be[h]   = amo2[h] ? (amo2_addr[h][2] ? 8'hF0 : 8'h0F) : be8[h == 0 ? D0 : D1];
        end
        wrote[2] = grant[N] && tgt_mem[N] && n_req_we;
        wr_addr[2] = addr[N];
        wr_be[2] = n_req_be;
        wrote[3] = grant[W] && tgt_mem[W];
        wr_addr[3] = addr[W];
        wr_be[3] = w_req_be;
    end
    // whether writer w touches hart h's reserved word (terms of the request alone)
    logic [1:0][3:0] touches_resv;
    always_comb
        for (int h = 0; h < 2; h++)
            for (int w = 0; w < 4; w++)
                touches_resv[h][w] = wr_addr[w][AB-1:3] == resv_word[h][AB-3:1]
                                     && (resv_word[h][0] ? |wr_be[w][7:4] : |wr_be[w][3:0]);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            v1 <= '0; v2 <= '0; err_q <= '0; amo1 <= '0; amo2 <= '0; amo_wr_now <= '0; resv <= '0; snoop_valid <= '0;
            ev_resv_end <= '0; io_ptr <= 1'b0; ptr_a <= '0; ptr_b <= '0; blk_v <= '0;
        end else begin
            // each port's pointer moves past the requester it took; the I/O bus alternates
            for (int b = 0; b < BANKS; b++) begin
                if (|pick_a[b]) ptr_a[b] <= after(pick_a[b]);
                if (|take_b[b]) ptr_b[b] <= after(take_b[b]);
                // port B's pick held back by port A's write: port A refuses writes to its unit next cycle
                blk_v[b] <= |pick_b[b] && !(|take_b[b]);
                for (int i = 0; i < 4; i++) if (pick_b[b][i]) blk_index[b] <= index[gb[i]];
            end
            if (io_grant[D0]) io_ptr <= 1'b1;
            if (io_grant[D1]) io_ptr <= 1'b0;
            // answers: stage 1 (the cycle after acceptance), stage 2 (the answer)
            v2 <= v1; kind2 <= kind1; bank2 <= bank1; port2 <= port1; half2 <= half1; scfail2 <= scfail1;
            for (int k = 0; k < NREQ; k++) begin
                v1[k]      <= grant[k];
                err_q[k]   <= grant[k] && tgt_err[k];
                kind1[k]   <= tgt_mem[k] ? ((k == D0 || k == D1) && d_req_op[k == D0 ? 0 : 1] == OP_SC ? K_SC : K_MEM)
                            : tgt_io[k] ? K_IO : K_NONE;
                bank1[k]   <= bank[k];
                half1[k]   <= addr[k][2];
                scfail1[k] <= !sc_ok[k];
                port1[k]   <= !on_port_a[k];
                if (v1[k] && kind1[k] == K_IO) io2[k] <= io_rsp_rdata;
            end
            // AMOs: accepted now, then writing two edges on (each bank's write registers loaded
            // from its hart's stage 1)
            amo2 <= amo1; amo2_addr <= amo1_addr;
            for (int b = 0; b < BANKS; b++) begin
                amo_wr_now[b] <= 1'b0;
                for (int h = 0; h < 2; h++)
                    if (amo1[h] && amo1_addr[h][5:4] == 2'(b)) begin
                        amo_wr_now[b]    <= 1'b1;
                        amo_hi[b]        <= amo1_addr[h][2];
                        amo_f5[b]        <= funct5_of(amo1_op[h]);
                        amo_operand_b[b] <= amo1_operand[h];
                        amo_index[b]     <= IB'({amo1_addr[h][UB+2:6], amo1_addr[h][3]});
                    end
            end
            for (int h = 0; h < 2; h++) begin
                amo1[h] <= grant[h == 0 ? D0 : D1] && tgt_mem[h == 0 ? D0 : D1] && is_amo[h == 0 ? D0 : D1];
                amo1_op[h] <= d_req_op[h];
                amo1_addr[h] <= d_req_addr[h];
                amo1_operand[h] <= d_req_wdata[h];
            end
            // snoops: each write's line to every other data cache, on its port for the writer
            for (int c = 0; c < 2; c++) begin
                snoop_valid[c] <= {wrote[3], wrote[2], wrote[1 - c]};
                snoop_line[c]  <= {wr_addr[3][31:4], wr_addr[2][31:4], wr_addr[1 - c][31:4]};
            end
            // reservations: an lr sets one (no write in its cycle can touch its word: the unit
            // rule); an sc, another requester's write to its word, an exception or a reset ends it
            for (int h = 0; h < 2; h++) begin
                logic mine, lr_now, sc_now, touched;
                mine    = |(pick_a[0][h] | pick_a[1][h] | pick_a[2][h] | pick_a[3][h]);   // lr and sc: port A
                lr_now  = mine && d_req_op[h] == OP_LR;
                sc_now  = mine && d_req_op[h] == OP_SC;
                touched = 1'b0;
                for (int w = 0; w < 4; w++) if (w != h && wrote[w] && touches_resv[h][w]) touched = 1'b1;
                ev_resv_end[h] <= resv[h] && !lr_now && !sc_now && touched;
                if (lr_now) begin
                    resv[h] <= 1'b1;
                    resv_word[h] <= d_req_addr[h][AB-1:2];
                end else if (sc_now || touched) resv[h] <= 1'b0;
                if (hart_exception[h] || !hart_rst_n[h]) resv[h] <= 1'b0;
            end
            // a hart held in reset: the answers owed to it are dropped
            for (int h = 0; h < 2; h++)
                if (!hart_rst_n[h]) begin
                    v1[h == 0 ? I0 : I1] <= 1'b0; v1[h == 0 ? D0 : D1] <= 1'b0;
                    v2[h == 0 ? I0 : I1] <= 1'b0; v2[h == 0 ? D0 : D1] <= 1'b0;
                    err_q[h == 0 ? I0 : I1] <= 1'b0; err_q[h == 0 ? D0 : D1] <= 1'b0;
                end
        end
    end

`ifndef SYNTHESIS
    // Each requester's main-memory flag (main_hint) must agree with its address: the fabric trusts it
    // and decodes no address itself (20.2). Simulation only.
    always_ff @(posedge clk)
        if (rst_n)
            for (int k = 0; k < NREQ; k++)
                if (valid[k])
                    assert (main_hint[k] == (addr[k][31:AB] == MEM_BASE[31:AB] && addr[k][AB-1:0] < AB'(MEM_BYTES)))
                    else $error("aster_fabric: requester %0d's main-memory flag disagrees with its address %h", k, addr[k]);
`endif
endmodule
