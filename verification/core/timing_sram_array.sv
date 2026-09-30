// Phase 18 timing probe: a standard-cell SRAM array of the kind the 18.6 L1
// caches will use (docs/phase18.md, SRAM timing plan), timed on its own so the
// L1 size can be chosen from measured slow-corner timing before the caches are
// designed.
//
// Behavior matches the core's synchronous-SRAM ports: a request's address (and
// a write, with byte enables) is taken at a clock edge, and the read word is
// available in the next cycle. `rdata_q` registers that word one edge later, so
// the array's read delay (address register -> decode/mux tree -> rdata_q) is a
// register-to-register path; subtracting it from the period gives the budget
// left for the logic that consumes read data in the core. Storage is flip-flops
// (area is therefore an upper bound for a latch array of the same size).
module timing_sram_array #(
    parameter int unsigned WORDS = 512              // 512 x 32 bits = 2 KiB
) (
    input  logic                       clk,
    input  logic                       req,
    input  logic                       we,
    input  logic [3:0]                 be,
    input  logic [$clog2(WORDS)-1:0]   addr,
    input  logic [31:0]                wdata,
    output logic [31:0]                rdata_q
);
    logic [31:0]               mem [WORDS];
    logic [$clog2(WORDS)-1:0]  addr_q;

    always_ff @(posedge clk) begin
        if (req) begin
            addr_q <= addr;
            if (we) begin
                for (int b = 0; b < 4; b++) begin
                    if (be[b]) mem[addr][8*b +: 8] <= wdata[8*b +: 8];
                end
            end
        end
        rdata_q <= mem[addr_q];
    end
endmodule

// The same array with a structured read: the registered address is decoded
// one-hot into word lines, and each output bit is an AND-OR over the words, so
// every word line drives 32 gates instead of each address bit steering a
// thousands-wide mux tree. Timed at two sizes to see how read time scales.
module timing_sram_array_andor #(
    parameter int unsigned WORDS = 512
) (
    input  logic                       clk,
    input  logic                       req,
    input  logic                       we,
    input  logic [3:0]                 be,
    input  logic [$clog2(WORDS)-1:0]   addr,
    input  logic [31:0]                wdata,
    output logic [31:0]                rdata_q
);
    logic [31:0]               mem [WORDS];
    logic [$clog2(WORDS)-1:0]  addr_q;
    logic [WORDS-1:0]          word_line;
    logic [31:0]               rdata;

    always_comb begin
        word_line = '0;
        word_line[addr_q] = 1'b1;
        rdata = '0;
        for (int w = 0; w < WORDS; w++) rdata |= mem[w] & {32{word_line[w]}};
    end

    always_ff @(posedge clk) begin
        if (req) begin
            addr_q <= addr;
            if (we) begin
                for (int b = 0; b < 4; b++) begin
                    if (be[b]) mem[addr][8*b +: 8] <= wdata[8*b +: 8];
                end
            end
        end
        rdata_q <= rdata;
    end
endmodule

module timing_sram_andor_2k (
    input logic clk, req, we, input logic [3:0] be, input logic [8:0] addr,
    input logic [31:0] wdata, output logic [31:0] rdata_q
);
    timing_sram_array_andor #(.WORDS(512)) array (.*);
endmodule

module timing_sram_andor_512b (
    input logic clk, req, we, input logic [3:0] be, input logic [6:0] addr,
    input logic [31:0] wdata, output logic [31:0] rdata_q
);
    timing_sram_array_andor #(.WORDS(128)) array (.*);
endmodule
