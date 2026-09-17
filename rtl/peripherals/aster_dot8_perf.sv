// AsterBench v6 supplement: two harts x (accept, wait, complete, retire).
// The existing primary-owned common window controls all three counter banks.
`timescale 1 ns / 1 ps
module aster_dot8_perf #(
    parameter int unsigned HART_COUNT = 2,
    parameter int unsigned CLOCK_HZ = 31_250_000
) (
    input logic clk, resetn, start, freeze, resume_counting,
    input logic [7:0] events,
    input logic [11:0] addr,
    output logic [31:0] rdata,
    output logic running,
    output logic [63:0] counters [0:7]
);
    initial if (HART_COUNT < 1 || HART_COUNT > 2) $error("dot8 supports one or two harts");
    // Register the events to break the route from the event sources into the
    // counter carry chains (measurement-only, one-cycle latency).
    logic [7:0] events_q;
    always_ff @(posedge clk) begin
        if (!resetn) events_q <= '0;
        else events_q <= events;
    end
    always_ff @(posedge clk) begin
        if (!resetn || start) begin
            for (int i = 0; i < 8; i++) counters[i] <= 0;
            running <= resetn && start;
        end else if (freeze) running <= 0;
        else if (resume_counting) running <= 1;
        else if (running) begin
            for (int i = 0; i < 8; i++)
                if (i < 4*HART_COUNT) counters[i] <= counters[i] + {63'b0, events_q[i]};
        end
    end
    always_comb begin
        rdata = 0;
        if (addr >= 12'h200 && addr < 12'h240 && addr[1:0] == 0) begin
            if (addr[2]) rdata = counters[addr[5:3]][63:32];
            else rdata = counters[addr[5:3]][31:0];
        end else case (addr)
            12'h280: rdata = {31'b0, running};
            12'h284: rdata = 6;
            12'h288: rdata = 1;
            12'h28c: rdata = 32'h0000_000b;
            12'h290: rdata = 32'hfe00_707f;
            12'h294: rdata = 4;
            12'h298: rdata = HART_COUNT;
            12'h29c: rdata = 1;
            12'h2a0: rdata = CLOCK_HZ;
            default: ;
        endcase
    end
endmodule
