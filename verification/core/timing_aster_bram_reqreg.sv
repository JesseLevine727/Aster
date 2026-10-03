// FPGA timing top: the Aster core with the two-cycle block RAM of
// timing_aster_bram.sv in its request-registered form (REQUEST_REGISTER = 1).
`timescale 1 ns / 1 ps
module timing_aster_bram_reqreg (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        meip,
    input  logic        mtip,
    input  logic        msip,
    input  logic [63:0] mtime,
    output logic [31:0] observe
);
    timing_aster_bram #(.REQUEST_REGISTER(1'b1)) block (.clk, .rst_n, .meip, .mtip, .msip, .mtime, .observe);
endmodule
