// Data array of an Aster L1 cache (milestone 18.6): WORDS words (1024: 4 KiB), a two-cycle
// read on one port and a byte-enabled write on the other — on the FPGA a block
// RAM with its output register. A read whose address is sampled at an edge
// (rd_en) is in rd_data during the cycle after the next edge; a read at that
// next edge does not disturb it. Reading and writing one word at the same edge
// is not used (the caches replay such a read).
`timescale 1 ns / 1 ps
module aster_l1_ram #(
    parameter int unsigned WORDS = 1024             // (20.5: the cache's capacity, a word an entry)
) (
    input  logic        clk,
    input  logic        rd_en,
    input  logic [$clog2(WORDS)-1:0] rd_addr,
    output logic [31:0] rd_data,
    input  logic [3:0]  wr_be,
    input  logic [$clog2(WORDS)-1:0] wr_addr,
    input  logic [31:0] wr_data
);
    logic [31:0] mem [WORDS];
    logic [31:0] read;
    always_ff @(posedge clk) begin
        if (rd_en) read <= mem[rd_addr];
        rd_data <= read;
        for (int lane = 0; lane < 4; lane++)
            if (wr_be[lane]) mem[wr_addr][8*lane +: 8] <= wr_data[8*lane +: 8];
    end
endmodule
