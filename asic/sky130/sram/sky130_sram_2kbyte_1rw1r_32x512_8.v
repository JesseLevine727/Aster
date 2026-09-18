// Black-box declaration of the SKY130 OpenRAM SRAM macro for the ASIC flow.
//
// Yosys must see this as a black box so it is instantiated, not synthesized
// into flops. The physical views (LEF/LIB/GDS) come from the PDK and are
// supplied through EXTRA_LEFS / EXTRA_LIBS / EXTRA_GDS_FILES. The behavioural
// model in rtl/memory/ is for Verilator only and must not be given to Yosys.
(* blackbox *)
module sky130_sram_2kbyte_1rw1r_32x512_8 (
    input  wire        clk0,
    input  wire        csb0,
    input  wire        web0,
    input  wire [3:0]  wmask0,
    input  wire [8:0]  addr0,
    input  wire [31:0] din0,
    output wire [31:0] dout0,
    input  wire        clk1,
    input  wire        csb1,
    input  wire [8:0]  addr1,
    output wire [31:0] dout1
);
endmodule
