// ABI 4: fourteen per-hart counters with an externally broadcast common window.
// See docs/phase6.md; deliberately separate from legacy v2/v3 register layouts.
`timescale 1 ns / 1 ps
module aster_coherent_perf #(
    parameter logic [31:0] BASE_ADDR = 32'h2000_3000,
    parameter int unsigned CLOCK_HZ = 31_250_000,
    parameter bit ENABLE_L1 = 1'b1,
    parameter bit SYNC_MEMORY = 1'b0,
    parameter int unsigned MEMORY_WAIT_CYCLES = SYNC_MEMORY ? 1 : 0,
    parameter int unsigned LINE_WORDS = 4,
    parameter int unsigned LINE_COUNT = 16
) (
    input logic clk,
    input logic resetn,
    input logic start,
    input logic freeze,
    input logic resume_counting,
    input logic [13:0] events,
    input logic [31:0] addr,
    output logic [31:0] rdata
);
    logic [63:0] counters [0:13];
    logic running;
    // Register the event inputs so the long route from each event source into
    // the 64-bit counter carry chains is broken. Measurement-only: this adds a
    // one-cycle event latency and changes no register or ABI.
    logic [13:0] events_q;
    wire [31:0] offset = addr - BASE_ADDR;
    always_ff @(posedge clk) begin
        if (!resetn) events_q <= '0;
        else events_q <= events;
    end
    always_ff @(posedge clk) begin
        if (!resetn || start) begin
            for (int i = 0; i < 14; i++) counters[i] <= 0;
            running <= resetn && start;
        end else if (freeze) running <= 0;
        else if (resume_counting) running <= 1;
        else if (running) begin
            for (int i = 0; i < 14; i++) counters[i] <= counters[i] + {63'b0, events_q[i]};
        end
    end
    always_comb begin
        rdata = 0;
        if (offset < 32'h70 && offset[1:0] == 0) begin
            if (offset[2]) rdata = counters[offset[6:3]][63:32];
            else rdata = counters[offset[6:3]][31:0];
        end else case (offset)
            32'h80: rdata = {31'b0, running};
            32'h84: rdata = 4;
            32'h88: rdata = CLOCK_HZ;
            32'h8c: rdata = {30'b0, SYNC_MEMORY, ENABLE_L1};
            32'h90: rdata = LINE_WORDS;
            32'h94: rdata = LINE_COUNT;
            32'h98: rdata = MEMORY_WAIT_CYCLES;
            32'h9c: rdata = 14;
            default: begin end
        endcase
    end
endmodule
