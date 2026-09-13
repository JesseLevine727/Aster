// AsterBench v5: separate DMA bank, driven by the existing CPU common window.
// Multi-byte/multi-line events are increments, not boolean activity indicators.
`timescale 1 ns / 1 ps
module aster_dma_perf #(
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
    input logic [2:0] increments [0:13],
    input logic [11:0] addr,
    output logic [31:0] rdata,
    output logic running,
    output logic [63:0] counters [0:13]
);
    always_ff @(posedge clk) begin
        if (!resetn || start) begin
            for (int i = 0; i < 14; i++) counters[i] <= 0;
            running <= resetn && start;
        end else if (freeze) running <= 0;
        else if (resume_counting) running <= 1;
        else if (running) begin
            for (int i = 0; i < 14; i++) counters[i] <= counters[i] + {61'b0, increments[i]};
        end
    end
    always_comb begin
        rdata = 0;
        if (addr >= 12'h100 && addr < 12'h170 && addr[1:0] == 0) begin
            if (addr[2]) rdata = counters[addr[6:3]][63:32];
            else rdata = counters[addr[6:3]][31:0];
        end else case (addr)
            12'h180: rdata = {31'b0, running};
            12'h184: rdata = 5;
            12'h188: rdata = CLOCK_HZ;
            12'h18c: rdata = {29'b0, 1'b1, SYNC_MEMORY, ENABLE_L1};
            12'h190: rdata = LINE_WORDS;
            12'h194: rdata = LINE_COUNT;
            12'h198: rdata = MEMORY_WAIT_CYCLES;
            12'h19c: rdata = 14;
            default: ;
        endcase
    end
endmodule
