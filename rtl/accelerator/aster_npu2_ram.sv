// The v2 NPU's operand buffers (docs/npu.md §4.4): a word-wide RAM with two
// write ports with byte enables and one read port, so the loader can write
// one memory word's bytes into the two buffer words it straddles in one cycle
// (an operand row at any byte alignment), and the array reads one word a
// cycle. Reads and writes never share a cycle (a buffer is loaded, then read;
// with two A strip buffers, 19.5, each has its own RAMs). BYTES is the word's
// width: 4, or 8 with a 64-bit memory port (19.5). Written to infer a
// true-dual-port block RAM with byte writes.
`timescale 1 ns / 1 ps
module aster_npu2_ram #(
    parameter int WORDS = 1024,
    parameter int BYTES = 4,
    parameter int AW = $clog2(WORDS)
) (
    input  logic          clk,
    // port A: read, or write with byte enables
    input  logic          a_en,
    input  logic [BYTES-1:0]   a_we,
    input  logic [AW-1:0]      a_addr,
    input  logic [8*BYTES-1:0] a_wdata,
    output logic [8*BYTES-1:0] a_rdata,
    // port B: write with byte enables
    input  logic [BYTES-1:0]   b_we,
    input  logic [AW-1:0]      b_addr,
    input  logic [8*BYTES-1:0] b_wdata
);
    logic [8*BYTES-1:0] mem [0:WORDS-1];

    always_ff @(posedge clk) begin
        if (a_en) begin
            for (int lane = 0; lane < BYTES; lane++)
                if (a_we[lane]) mem[a_addr][8*lane +: 8] <= a_wdata[8*lane +: 8];
            a_rdata <= mem[a_addr];
        end
    end

    always_ff @(posedge clk) begin
        for (int lane = 0; lane < BYTES; lane++)
            if (b_we[lane]) mem[b_addr][8*lane +: 8] <= b_wdata[8*lane +: 8];
    end
endmodule
