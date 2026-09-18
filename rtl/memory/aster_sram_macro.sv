// Adapter from the single-port aster_ram interface to the SKY130 OpenRAM
// 1rw1r SRAM macro shipped with the PDK. The macro is a black box for
// synthesis; for simulation it is replaced by its behavioural model.
module aster_sram_macro #(
    parameter logic [31:0] BASE_ADDR = 32'h1000_0000,
    parameter int unsigned DEPTH_WORDS = 512
) (
    input  logic        clk,
    input  logic [31:0] addr,
    input  logic [31:0] wdata,
    input  logic [3:0]  wstrb,
    input  logic        we,
    output logic [31:0] rdata
);
    localparam int unsigned ADDR_WIDTH = 9;
    localparam logic [31:0] DEPTH_BYTES = 32'(DEPTH_WORDS * 4);
    logic [ADDR_WIDTH-1:0] word_index;
    logic                  select;

    initial if (DEPTH_WORDS != 512)
        $error("aster_sram_macro maps the fixed 512x32 sky130_sram_2kbyte macro");

    always_comb begin
        word_index = addr[ADDR_WIDTH+1:2] - BASE_ADDR[ADDR_WIDTH+1:2];
        select = (addr - BASE_ADDR) < DEPTH_BYTES;
    end

    /* verilator lint_off PINCONNECTEMPTY */
    sky130_sram_2kbyte_1rw1r_32x512_8 macro (
        .clk0(clk),
        .csb0(~select),
        .web0(~we),
        .wmask0(wstrb),
        .addr0(word_index),
        .din0(wdata),
        .dout0(rdata),
        .clk1(clk),
        .csb1(1'b1),
        .addr1(9'b0),
        .dout1()
    );
    /* verilator lint_on PINCONNECTEMPTY */
endmodule
