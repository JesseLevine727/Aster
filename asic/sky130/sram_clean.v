// Functional stand-in for the SKY130 OpenRAM 2 KiB macro.
//
// The vendor model drives `dout0` to X for part of every cycle (T_HOLD/DELAY
// timing) which stalls the CPU when the SoC samples the registered read. For
// functional gate-level simulation we use a plain one-cycle synchronous model
// with the same ports and parameters; timing is supplied by the SDF.
module sky130_sram_2kbyte_1rw1r_32x512_8 #(
    parameter NUM_WMASKS = 4,
    parameter DATA_WIDTH = 32,
    parameter ADDR_WIDTH = 9,
    parameter RAM_DEPTH = 512
) (
    input  wire                  clk0,
    input  wire                  csb0,
    input  wire                  web0,
    input  wire [NUM_WMASKS-1:0] wmask0,
    input  wire [ADDR_WIDTH-1:0] addr0,
    input  wire [DATA_WIDTH-1:0] din0,
    output reg  [DATA_WIDTH-1:0] dout0,
    input  wire                  clk1,
    input  wire                  csb1,
    input  wire [ADDR_WIDTH-1:0] addr1,
    output reg  [DATA_WIDTH-1:0] dout1
);
    reg [DATA_WIDTH-1:0] mem [0:RAM_DEPTH-1];

    always @(posedge clk0) begin
        if (!csb0) begin
            if (!web0) begin
                if (wmask0[0]) mem[addr0][7:0]   <= din0[7:0];
                if (wmask0[1]) mem[addr0][15:8]  <= din0[15:8];
                if (wmask0[2]) mem[addr0][23:16] <= din0[23:16];
                if (wmask0[3]) mem[addr0][31:24] <= din0[31:24];
            end else begin
                dout0 <= mem[addr0];
            end
        end
    end

    always @(posedge clk1) begin
        if (!csb1)
            dout1 <= mem[addr1];
    end
endmodule
