// Phase 18 two-port CPU shell DUT: the Aster core (rtl/aster_core) on the
// shell's ports (tb_core_ports.cpp). The wrapper drives the core's reset and
// ties its interrupt inputs low (they are used from 18.3); `trap` is the
// core's `trapped` (18.1: a trap record has retired and the core stopped);
// chk_i_redirect comes from the core's fetch unit. The core has no self-test
// mutants, so `selftest` is unused.
`timescale 1 ns / 1 ps
module shell_aster_ports (
    input  logic        clk,
    input  logic        resetn,
    input  logic [3:0]  selftest,
    output logic        trap,
    output logic        chk_i_redirect,
    // instruction port
    output logic        i_req_valid,
    output logic [31:2] i_req_addr,
    input  logic        i_req_ready,
    input  logic        i_rsp_valid,
    input  logic [31:0] i_rsp_data,
    input  logic        i_rsp_error,
    // data port
    output logic        d_req_valid,
    output logic [3:0]  d_req_op,
    output logic [31:0] d_req_addr,
    output logic [31:0] d_req_wdata,
    output logic [3:0]  d_req_be,
    input  logic        d_req_ready,
    input  logic        d_rsp_valid,
    input  logic [31:0] d_rsp_rdata,
    input  logic        d_rsp_error,
    // RVFI
    output logic        rvfi_valid,
    output logic [63:0] rvfi_order,
    output logic [31:0] rvfi_insn,
    output logic        rvfi_trap,
    output logic [31:0] rvfi_pc_rdata,
    output logic [4:0]  rvfi_rd_addr,
    output logic [31:0] rvfi_rd_wdata,
    output logic [31:0] rvfi_mem_addr,
    output logic [3:0]  rvfi_mem_rmask,
    output logic [3:0]  rvfi_mem_wmask,
    output logic [31:0] rvfi_mem_rdata,
    output logic [31:0] rvfi_mem_wdata
);
    logic unused;
    assign unused = ^selftest;

    /* verilator lint_off PINCONNECTEMPTY */
    aster_core #(.RESET_VECTOR(32'h8000_0000), .HART_ID(32'd0)) core (
        .clk, .rst_n(resetn), .meip(1'b0), .mtip(1'b0), .msip(1'b0),
        .i_req_valid, .i_req_addr, .i_req_ready, .i_rsp_valid, .i_rsp_data, .i_rsp_error,
        .d_req_valid, .d_req_op, .d_req_addr, .d_req_wdata, .d_req_be,
        .d_req_ready, .d_rsp_valid, .d_rsp_rdata, .d_rsp_error,
        .trapped(trap), .chk_i_redirect,
        .rvfi_valid, .rvfi_order, .rvfi_insn, .rvfi_trap, .rvfi_halt(), .rvfi_intr(), .rvfi_mode(), .rvfi_ixl(),
        .rvfi_rs1_addr(), .rvfi_rs2_addr(), .rvfi_rs1_rdata(), .rvfi_rs2_rdata(),
        .rvfi_rd_addr, .rvfi_rd_wdata, .rvfi_pc_rdata, .rvfi_pc_wdata(),
        .rvfi_mem_addr, .rvfi_mem_rmask, .rvfi_mem_wmask, .rvfi_mem_rdata, .rvfi_mem_wdata
    );
    /* verilator lint_on PINCONNECTEMPTY */
endmodule
