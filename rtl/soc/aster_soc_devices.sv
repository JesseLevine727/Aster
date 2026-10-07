// The Phase 20 SoC's devices (milestone 20.2; docs/soc.md §3, §7, §8), behind
// the fabric's I/O bus. The bus's request is registered here (io_q): a device
// sees it in the cycle after its acceptance, takes a write at that cycle's
// edge and answers a read combinationally in it, so the fabric still has the
// answer in the cycle after acceptance (soc.md §13, 20.2). Every device takes
// word accesses only (the data caches' windows are word-only, soc.md §3).
//
// - 0x2000_0000 UART: a store to the TX word appends its low byte to the
//   console (console_we/console_byte); reads 0.
// - 0x2000_1000 timer, v1's ABI 1 (docs/timer.md): TIME, a free-running cycle
//   count, also the cores' mtime; COMPARE; CONTROL (bit 0 enable, bit 1 clear
//   pending); STATUS; ABI; CLOCK_HZ; its interrupt is source 0.
// - 0x2000_2000 hart control, v1's layout: ID (the requesting hart),
//   SECONDARY_RUN (hart 0's stores only), HART_COUNT, STATUS (bits 0-1 the
//   harts running; v1's trapped bits read 0), the TO_HART1 mailbox (hart 0
//   writes it) and TO_HART0 (hart 1 writes it), both cleared when hart 0
//   holds hart 1 (v1 cleared them at hart 1's stop); v1's warm-stop and
//   fault words read 0.
// - 0x2000_3000 and 0x2000_3100: each hart's counters, v1's ABI 4 (docs/phase6.md):
//   fourteen counters at 0x00-0x6C (cycles, retired, memory transactions, I$
//   accesses, I$ misses, D$ data accesses, D$ data misses, backing
//   transactions, completed A instructions, successful sc, failed sc, dirty
//   interventions (0), invalidations, write-back words (0)); the command at
//   0x2000_3080 (hart 0's, its low byte exactly 1 START, 2 FREEZE or 4
//   RESUME, as v1), common to every counter page here; metadata at
//   0x84-0x9C.
// - 0x2000_3200 the DOT8 counters, v1's ABI 6 layout: per hart accepted,
//   waited (0: the core never makes a dot8 wait), completed, retired.
// - 0x2000_3300-0x2000_34FF the fabric's counters (soc.md §8), each 8 bytes:
//   0x300 + 8k for k = 0-6 accepted requests and k = 7-13 cycles waited, per
//   requester (I0, D0, I1, D1, N, R, W); k = 14-17 each bank's reads (loads,
//   lr, refills, AMOs), 18-21 its writes (stores, sc, AMOs), 22-25 its
//   conflicts (cycles a request to it waited); 26-31 snoops delivered and
//   32-37 lines invalidated, cache 0's ports 0-2 then cache 1's; 38-39 each
//   hart's reservations ended by another requester; 40 AMOs; 41-47 each
//   requester's longest wait, in cycles (I0, D0, I1, D1, N, R, W); at 0x4F0
//   the ABI (1), 0x4F4 the count (48), 0x4F8 running.
// - 0x2000_4000 the interrupt controller, v1's ABI 1 (its own module): sources
//   0 the timer, 1 the DMA, 2 the NPU, 3 software; ENABLE0/ENABLE1 drive each
//   hart's MEIP.
// Counters count in 48 bits (soc.md §2) and read as 64; each counts its
// event registered (a cycle late, measurement only), from START to FREEZE.
`timescale 1 ns / 1 ps
module aster_soc_devices #(
    parameter int unsigned CLK_HZ = 100_000_000,
    parameter int unsigned HARTS = 2,
    parameter int unsigned WAIT = 0
) (
    input  logic        clk,
    input  logic        rst_n,                  // the run's reset
    // the I/O bus, in its cycle of acceptance (the fabric's io_req_*)
    input  logic        io_valid,
    input  logic        io_hart,
    input  logic [3:0]  io_op,
    input  logic [31:0] io_addr,
    input  logic [31:0] io_wdata,
    output logic [31:0] io_rdata,               // the cycle after
    output logic        io_sel,                 // the cycle after: this answer is a device's (else the NPU's)
    // the console, the second hart, the timer and interrupts
    output logic        console_we,
    output logic [7:0]  console_byte,
    output logic        secondary_run,
    output logic [63:0] mtime,
    input  logic        npu_irq,
    input  logic        dma_irq,
    output logic [1:0]  meip,
    output logic        window_start,           // the counters' command
    output logic        window_freeze,
    // events, each cycle (per hart unless noted)
    input  logic [1:0]       ev_retired, ev_mem_txn, ev_i_access, ev_i_miss, ev_d_access, ev_d_miss,
    input  logic [1:0]       ev_amo_done, ev_sc_ok, ev_sc_fail, ev_inval, ev_dot8,
    input  logic [1:0][1:0]  ev_backing,         // 0-2 a cycle
    input  logic [6:0]       ev_accept, ev_wait, // per requester
    input  logic [3:0][1:0]  ev_bank_read,       // 0-2 a cycle
    input  logic [3:0]       ev_bank_write, ev_bank_conflict,
    input  logic [1:0][2:0]  ev_snoop, ev_snoop_hit,
    input  logic [1:0]       ev_resv_end,
    input  logic [1:0]       ev_amo              // 0-2 a cycle
);
    import aster_core_pkg::OP_STORE;

    // ---- the request, registered ----
    logic        q_valid, q_hart, q_write;
    logic [31:0] q_addr, q_wdata;
    always_ff @(posedge clk) begin
        q_valid <= rst_n && io_valid && io_addr[31:12] != 20'h4_0000;    // (the NPU's page is not a device's)
        q_hart  <= io_hart;
        q_write <= io_op == OP_STORE;
        q_addr  <= io_addr;
        q_wdata <= io_wdata;
    end
    assign io_sel = q_valid;
    logic [19:0] page;
    logic [11:0] off;
    logic        wr;
    assign page = q_addr[31:12];
    assign off  = q_addr[11:0];
    assign wr   = q_valid && q_write;

    // ---- UART ----
    assign console_we   = wr && page == 20'h2_0000 && off == 12'h000;
    assign console_byte = q_wdata[7:0];

    // ---- timer (ABI 1) ----
    logic [63:0] time_count, compare;
    logic        t_enabled, t_pending;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            time_count <= '0; compare <= '0; t_enabled <= 1'b0; t_pending <= 1'b0;
        end else begin
            time_count <= time_count + 64'd1;
            if (wr && page == 20'h2_0001 && off == 12'h008) compare[31:0] <= q_wdata;
            if (wr && page == 20'h2_0001 && off == 12'h00C) compare[63:32] <= q_wdata;
            if (t_enabled && time_count + 64'd1 == compare) t_pending <= 1'b1;
            if (wr && page == 20'h2_0001 && off == 12'h010) begin
                t_enabled <= q_wdata[0];
                if (q_wdata[1]) t_pending <= 1'b0;
            end
        end
    end
    assign mtime = time_count;

    // ---- hart control ----
    logic [31:0] to_hart1, to_hart0;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            secondary_run <= 1'b0; to_hart1 <= '0; to_hart0 <= '0;
        end else if (wr && page == 20'h2_0002) begin
            if (off == 12'h004 && !q_hart) secondary_run <= q_wdata[0] && HARTS == 2;
            if (off == 12'h004 && !q_hart && !q_wdata[0]) begin to_hart1 <= '0; to_hart0 <= '0; end
            else begin
                if (off == 12'h010 && !q_hart) to_hart1 <= q_wdata;
                if (off == 12'h014 && q_hart) to_hart0 <= q_wdata;
            end
        end
    end

    // ---- the interrupt controller (v1's ABI 1, docs/interrupts.md) ----
    // Sources latched on their rising edges; ENABLE0/1 (0x00, 0x04), PENDING
    // (0x08, write 1 to clear), ACTIVE0/1 (0x0C, 0x10), RAISE (0x14, the
    // software source), ABI (0x18) and SOURCES (0x1C).
    logic [3:0] irq_pending, irq_enable0, irq_enable1, irq_sources_q, irq_sources;
    logic [31:0] irq_rdata;
    assign irq_sources = {1'b0, npu_irq, dma_irq, t_pending};
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            irq_pending <= '0; irq_enable0 <= '0; irq_enable1 <= '0; irq_sources_q <= '0;
        end else begin
            logic [3:0] next;
            next = irq_pending | (irq_sources & ~irq_sources_q);
            irq_sources_q <= irq_sources;
            if (wr && page == 20'h2_0004 && off == 12'h014) next[3] = 1'b1;
            if (wr && page == 20'h2_0004 && off == 12'h008) next = next & ~q_wdata[3:0];
            irq_pending <= next;
            if (wr && page == 20'h2_0004 && off == 12'h000) irq_enable0 <= q_wdata[3:0];
            if (wr && page == 20'h2_0004 && off == 12'h004) irq_enable1 <= q_wdata[3:0];
        end
    end
    always_comb
        unique case (off)
            12'h000: irq_rdata = {28'b0, irq_enable0};
            12'h004: irq_rdata = {28'b0, irq_enable1};
            12'h008: irq_rdata = {28'b0, irq_pending};
            12'h00C: irq_rdata = {28'b0, irq_pending & irq_enable0};
            12'h010: irq_rdata = {28'b0, irq_pending & irq_enable1};
            12'h018: irq_rdata = 32'd1;
            12'h01C: irq_rdata = 32'd4;
            default: irq_rdata = '0;
        endcase
    assign meip = {|(irq_pending & irq_enable1), |(irq_pending & irq_enable0)};

    // ---- the counters: the common command, the events registered ----
    logic counting;
    logic command, resume;
    assign command       = wr && page == 20'h2_0003 && off == 12'h080 && !q_hart;
    assign window_start  = command && q_wdata[7:0] == 8'd1;
    assign window_freeze = command && q_wdata[7:0] == 8'd2;
    assign resume        = command && q_wdata[7:0] == 8'd4;
    // per hart: 14 ABI 4 counters (11 counting), 4 DOT8 (one counting); the fabric's 41
    localparam int NH = 12;                     // counted a hart: the 11 ABI 4 events and dot8
    localparam int NF = 41;                     // the fabric's counted events (the longest wait apart)
    logic [1:0][NH-1:0][1:0] h_ev;              // each event's increment, 0-2
    logic [NF-1:0][1:0]      f_ev;
    logic [1:0][NH-1:0][47:0] h_count;
    logic [NF-1:0][47:0]      f_count;
    logic [6:0][15:0]         streak, longest;    // each requester's (one compare each: no chain of maxima)
    always_ff @(posedge clk) begin
        for (int h = 0; h < 2; h++)
            h_ev[h] <= {{1'b0, ev_dot8[h]}, {1'b0, ev_inval[h]}, {1'b0, ev_sc_fail[h]}, {1'b0, ev_sc_ok[h]},
                        {1'b0, ev_amo_done[h]}, ev_backing[h], {1'b0, ev_d_miss[h]}, {1'b0, ev_d_access[h]},
                        {1'b0, ev_i_miss[h]}, {1'b0, ev_i_access[h]}, {1'b0, ev_mem_txn[h]}, {1'b0, ev_retired[h]}};
        for (int k = 0; k < 7; k++) begin
            f_ev[k]     <= {1'b0, ev_accept[k]};
            f_ev[7 + k] <= {1'b0, ev_wait[k]};
        end
        for (int b = 0; b < 4; b++) begin
            f_ev[14 + b] <= ev_bank_read[b];
            f_ev[18 + b] <= {1'b0, ev_bank_write[b]};
            f_ev[22 + b] <= {1'b0, ev_bank_conflict[b]};
        end
        for (int c = 0; c < 2; c++)
            for (int p = 0; p < 3; p++) begin
                f_ev[26 + 3 * c + p] <= {1'b0, ev_snoop[c][p]};
                f_ev[32 + 3 * c + p] <= {1'b0, ev_snoop_hit[c][p]};
            end
        f_ev[38] <= {1'b0, ev_resv_end[0]};
        f_ev[39] <= {1'b0, ev_resv_end[1]};
        f_ev[40] <= ev_amo;
    end
    // cycles: each hart's counter 0 counts every cycle of the window (a common interval)
    always_ff @(posedge clk) begin
        if (!rst_n || window_start) begin
            h_count <= '0; f_count <= '0; streak <= '0; longest <= '0;
            counting <= rst_n && window_start;
        end else if (window_freeze) counting <= 1'b0;
        else if (resume) counting <= 1'b1;
        else if (counting) begin
            for (int h = 0; h < 2; h++)
                for (int i = 0; i < NH; i++) h_count[h][i] <= h_count[h][i] + 48'(h_ev[h][i]);
            for (int i = 0; i < NF; i++) f_count[i] <= f_count[i] + 48'(f_ev[i]);
            for (int k = 0; k < 7; k++) begin
                // (a streak and a longest wait saturate at 65,535 cycles)
                streak[k] <= f_ev[7 + k][0] ? (streak[k] == 16'hFFFF ? streak[k] : streak[k] + 16'd1) : 16'd0;
                if (f_ev[7 + k][0] && streak[k] >= longest[k] && longest[k] != 16'hFFFF) longest[k] <= streak[k] + 16'd1;
            end
        end
    end
    // cycles of the window, for the ABI 4 counter 0 of both harts
    logic [47:0] window_cycles;
    always_ff @(posedge clk)
        if (!rst_n || window_start) window_cycles <= '0;
        else if (counting) window_cycles <= window_cycles + 48'd1;

    // ---- reads (combinational from the registered request) ----
    function automatic logic [31:0] half(input logic [47:0] v, input logic hi);
        return hi ? {16'b0, v[47:32]} : v[31:0];
    endfunction
    always_comb begin
        io_rdata = '0;
        unique case (page)
            20'h2_0001: unique case (off)
                12'h000: io_rdata = time_count[31:0];
                12'h004: io_rdata = time_count[63:32];
                12'h008: io_rdata = compare[31:0];
                12'h00C: io_rdata = compare[63:32];
                12'h014: io_rdata = {30'b0, t_enabled, t_pending};
                12'h018: io_rdata = 32'd1;
                12'h01C: io_rdata = CLK_HZ;
                default: io_rdata = '0;
            endcase
            20'h2_0002: unique case (off)
                12'h000: io_rdata = {31'b0, q_hart};
                12'h004: io_rdata = {31'b0, secondary_run};
                12'h008: io_rdata = HARTS;
                12'h00C: io_rdata = {30'b0, secondary_run, 1'b1};
                12'h010: io_rdata = to_hart1;
                12'h014: io_rdata = to_hart0;
                default: io_rdata = '0;
            endcase
            20'h2_0003: begin
                if (off < 12'h200) begin                       // a hart's ABI 4 bank
                    logic h;
                    logic [7:0] o;
                    h = off[8];
                    o = off[7:0];
                    if (o < 8'h70) unique case (o[6:3])
                        4'd0:  io_rdata = half(window_cycles, o[2]);
                        4'd1:  io_rdata = half(h_count[h][0], o[2]);     // retired
                        4'd2:  io_rdata = half(h_count[h][1], o[2]);     // memory transactions
                        4'd3:  io_rdata = half(h_count[h][2], o[2]);     // I$ accesses
                        4'd4:  io_rdata = half(h_count[h][3], o[2]);     // I$ misses
                        4'd5:  io_rdata = half(h_count[h][4], o[2]);     // D$ accesses
                        4'd6:  io_rdata = half(h_count[h][5], o[2]);     // D$ misses
                        4'd7:  io_rdata = half(h_count[h][6], o[2]);     // backing transactions
                        4'd8:  io_rdata = half(h_count[h][7], o[2]);     // completed A instructions
                        4'd9:  io_rdata = half(h_count[h][8], o[2]);     // successful sc
                        4'd10: io_rdata = half(h_count[h][9], o[2]);     // failed sc
                        4'd12: io_rdata = half(h_count[h][10], o[2]);    // invalidations
                        default: io_rdata = '0;                          // dirty interventions, write-back words
                    endcase
                    else unique case (o)
                        8'h80: io_rdata = {31'b0, counting};
                        8'h84: io_rdata = 32'd4;
                        8'h88: io_rdata = CLK_HZ;
                        8'h8C: io_rdata = 32'h3;                         // caches, synchronous memory
                        8'h90: io_rdata = 32'd4;                         // line words
                        8'h94: io_rdata = 32'd256;                       // lines
                        8'h98: io_rdata = 1 + WAIT;                      // memory waits: v1's sync memory read 1
                        8'h9C: io_rdata = 32'd14;
                        default: io_rdata = '0;
                    endcase
                end else if (off < 12'h300) begin              // DOT8, ABI 6
                    if (off < 12'h240) begin
                        logic h;
                        logic [1:0] e;
                        h = off[5];                              // (hart * 4 + event) * 8
                        e = off[4:3];
                        io_rdata = e == 2'd1 ? '0 : half(h_count[h][11], off[2]);
                    end else unique case (off)
                        12'h280: io_rdata = {31'b0, counting};
                        12'h284: io_rdata = 32'd6;
                        12'h288: io_rdata = 32'd1;
                        12'h28C: io_rdata = 32'h0000_000B;
                        12'h290: io_rdata = 32'hFE00_707F;
                        12'h294: io_rdata = 32'd4;
                        12'h298: io_rdata = HARTS;
                        12'h29C: io_rdata = 32'd1;
                        12'h2A0: io_rdata = CLK_HZ;
                        default: io_rdata = '0;
                    endcase
                end else begin                                 // the fabric's counters, 0x300-0x4FF
                    logic [8:0] i;
                    i = 9'((off - 12'h300) >> 3);
                    if (off >= 12'h4F0) unique case (off)
                        12'h4F0: io_rdata = 32'd1;                          // the fabric counters' ABI
                        12'h4F4: io_rdata = 32'd48;                         // counters
                        12'h4F8: io_rdata = {31'b0, counting};
                        default: io_rdata = '0;
                    endcase
                    else if (i < 9'd41) io_rdata = half(f_count[i[5:0]], off[2]);
                    else if (i >= 9'd41 && i < 9'd48) io_rdata = off[2] ? '0 : {16'b0, longest[i - 9'd41]};
                end
            end
            20'h2_0004: io_rdata = irq_rdata;
            default: io_rdata = '0;                            // the UART, the DMA's page (20.3), others
        endcase
    end

    logic unused;
    assign unused = ^{q_addr[31:0], dma_irq};
endmodule
