// The v2 NPU's operand buffers (docs/npu.md §4.4): a word-wide RAM with two
// write ports with byte enables and one read port, so the loader can write
// one memory word's bytes into the two buffer words it straddles in one cycle
// (an operand row at any byte alignment), and the array reads one word a
// cycle. Reads and writes never share a cycle (the buffers are loaded, then
// read). Written to infer a true-dual-port block RAM with byte writes.
`timescale 1 ns / 1 ps
module aster_npu2_ram #(
    parameter int WORDS = 1024,
    parameter int AW = $clog2(WORDS)
) (
    input  logic          clk,
    // port A: read, or write with byte enables
    input  logic          a_en,
    input  logic [3:0]    a_we,
    input  logic [AW-1:0] a_addr,
    input  logic [31:0]   a_wdata,
    output logic [31:0]   a_rdata,
    // port B: write with byte enables
    input  logic [3:0]    b_we,
    input  logic [AW-1:0] b_addr,
    input  logic [31:0]   b_wdata
);
    logic [31:0] mem [0:WORDS-1];

    always_ff @(posedge clk) begin
        if (a_en) begin
            for (int lane = 0; lane < 4; lane++)
                if (a_we[lane]) mem[a_addr][8*lane +: 8] <= a_wdata[8*lane +: 8];
            a_rdata <= mem[a_addr];
        end
    end

    always_ff @(posedge clk) begin
        for (int lane = 0; lane < 4; lane++)
            if (b_we[lane]) mem[b_addr][8*lane +: 8] <= b_wdata[8*lane +: 8];
    end
endmodule
