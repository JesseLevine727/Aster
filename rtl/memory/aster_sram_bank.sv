// Bank of SKY130 OpenRAM 2 KiB macros backing a large Aster memory.
//
// The v1.3 system needs 64 KiB of ROM and RAM. That is 32 macros per memory,
// far past what synthesised flops can hold, so the ASIC build maps the memory
// onto a bank of `sky130_sram_2kbyte_1rw1r_32x512_8` macros: the low nine bits
// of the word index select the word inside a macro and the remaining bits
// select the macro. The macro registers its own read, so the bank returns the
// registered data and callers must not register again.
module aster_sram_bank #(
    parameter logic [31:0] BASE_ADDR = 32'h0000_0000,
    parameter int unsigned DEPTH_WORDS = 16_384
) (
    input  logic        clk,
    input  logic [31:0] addr,
    input  logic [31:0] wdata,
    input  logic [3:0]  wstrb,
    input  logic        we,
    output logic [31:0] rdata
);
    localparam int unsigned WORD_WIDTH = 9;
    localparam int unsigned MACROS = DEPTH_WORDS / 512;
    localparam int unsigned SEL_WIDTH = (MACROS <= 1) ? 1 : $clog2(MACROS);
    localparam logic [31:0] DEPTH_BYTES = 32'(DEPTH_WORDS * 4);

    logic [31:0]                        offset;
    logic [WORD_WIDTH+SEL_WIDTH-1:0]    word;
    logic [SEL_WIDTH-1:0]               sel;
    logic                               in_range;
    logic [SEL_WIDTH-1:0]   sel_q;
    logic                   in_range_q;
    logic [31:0]            dout [0:MACROS-1];

    initial if (DEPTH_WORDS % 512 != 0)
        $error("aster_sram_bank requires a whole number of 512-word macros");

    always_comb begin
        offset = addr - BASE_ADDR;
        word = offset[WORD_WIDTH+SEL_WIDTH+1:2];
        sel = word[WORD_WIDTH+SEL_WIDTH-1:WORD_WIDTH];
        in_range = offset < DEPTH_BYTES;
    end

    // The macro read is registered, so align the output select with it.
    always_ff @(posedge clk) begin
        sel_q <= sel;
        in_range_q <= in_range;
    end

    for (genvar index = 0; index < MACROS; index++) begin : g_macro
        /* verilator lint_off PINCONNECTEMPTY */
        sky130_sram_2kbyte_1rw1r_32x512_8 macro (
            .clk0(clk),
            .csb0(~(in_range && sel == SEL_WIDTH'(index))),
            .web0(~we),
            .wmask0(wstrb),
            .addr0(word[WORD_WIDTH-1:0]),
            .din0(wdata),
            .dout0(dout[index]),
            .clk1(clk),
            .csb1(1'b1),
            .addr1(9'b0),
            .dout1()
        );
        /* verilator lint_on PINCONNECTEMPTY */
    end

    always_comb begin
        rdata = 32'd0;
        if (in_range_q)
            rdata = dout[sel_q];
    end
endmodule
