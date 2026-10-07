// Vivado module-reference shim for the Phase 20 SoC's board design (milestone
// 20.2's in-context build; fpga/pynq_z1/build_aster_core.tcl with design soc):
// a block design needs a Verilog top. All behavior is in rtl/soc/aster_soc.sv,
// which verification/aster_soc/tb_soc.cpp simulates. The clock interface's
// FREQ_HZ is fixed here (the block design will not let the build override it):
// 100 MHz, FCLK0.
module aster_soc_ip #(
    parameter integer CLK_HZ = 100000000,
    parameter integer NPU_A_STRIPS = 2,                // 19.5's options, as adopted
    parameter integer NPU_PORT_BYTES = 8,
    parameter integer NPU_DIM = 8,
    parameter integer HARTS = 2,
    parameter integer SHELL_PAGE = 0,                    // 1: the regression build (aster_soc.sv)
    parameter integer FABRIC_D_ON_B = 1,                 // the fabric's second chance (aster_soc.sv)
    parameter integer NPU_BUFFER = 0                     // (aster_soc.sv)
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK",
       X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axi, ASSOCIATED_RESET aresetn, FREQ_HZ 100000000" *)
    input wire aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST",
       X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input wire aresetn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR",
       X_INTERFACE_PARAMETER = "PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 18" *)
    input wire [17:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *)
    input wire s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *)
    output wire s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA" *)
    input wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB" *)
    input wire [3:0] s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID" *)
    input wire s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY" *)
    output wire s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP" *)
    output wire [1:0] s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID" *)
    output wire s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY" *)
    input wire s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR" *)
    input wire [17:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *)
    input wire s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *)
    output wire s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)
    output wire [1:0] s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)
    output wire s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)
    input wire s_axi_rready
);
    aster_soc #(.CLK_HZ(CLK_HZ), .NPU_A_STRIPS(NPU_A_STRIPS), .NPU_PORT_BYTES(NPU_PORT_BYTES), .NPU_DIM(NPU_DIM),
                .HARTS(HARTS), .SHELL_PAGE(SHELL_PAGE), .FABRIC_D_ON_B(FABRIC_D_ON_B),
                .NPU_BUFFER(NPU_BUFFER)) implementation (
        .aclk(aclk), .aresetn(aresetn),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb),
        .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp), .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp),
        .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(s_axi_rready)
    );
endmodule
