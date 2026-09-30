// Timing/area wrapper for the Aster core (rtl/aster_core, milestone 18.1): the
// core with its two ports and interrupt inputs exposed and its RVFI and
// verification outputs left open (they are not hardware; synthesis removes
// them). Synthesized out of context by scripts/timing/vivado_ooc.tcl (register
// to register only) and as a SKY130 block by scripts/run_asic.py --design
// core_aster, where the SDC's input and output delays also time the paths
// from the memory's answer back to the next request (docs/cpu.md §4).
`timescale 1 ns / 1 ps
module timing_aster (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        meip,
    input  logic        mtip,
    input  logic        msip,
    output logic        trapped,
    output logic        i_req_valid,
    output logic [31:2] i_req_addr,
    input  logic        i_req_ready,
    input  logic        i_rsp_valid,
    input  logic [31:0] i_rsp_data,
    input  logic        i_rsp_error,
    output logic        d_req_valid,
    output logic [3:0]  d_req_op,
    output logic [31:0] d_req_addr,
    output logic [31:0] d_req_wdata,
    output logic [3:0]  d_req_be,
    input  logic        d_req_ready,
    input  logic        d_rsp_valid,
    input  logic [31:0] d_rsp_rdata,
    input  logic        d_rsp_error
);
    /* verilator lint_off PINCONNECTEMPTY */
    aster_core #(.RESET_VECTOR(32'h8000_0000), .HART_ID(32'd0)) core (
        .clk, .rst_n, .meip, .mtip, .msip,
        .i_req_valid, .i_req_addr, .i_req_ready, .i_rsp_valid, .i_rsp_data, .i_rsp_error,
        .d_req_valid, .d_req_op, .d_req_addr, .d_req_wdata, .d_req_be,
        .d_req_ready, .d_rsp_valid, .d_rsp_rdata, .d_rsp_error,
        .trapped, .chk_i_redirect(),
        .rvfi_valid(), .rvfi_order(), .rvfi_insn(), .rvfi_trap(), .rvfi_halt(), .rvfi_intr(), .rvfi_mode(),
        .rvfi_ixl(), .rvfi_rs1_addr(), .rvfi_rs2_addr(), .rvfi_rs1_rdata(), .rvfi_rs2_rdata(), .rvfi_rd_addr(),
        .rvfi_rd_wdata(), .rvfi_pc_rdata(), .rvfi_pc_wdata(), .rvfi_mem_addr(), .rvfi_mem_rmask(),
        .rvfi_mem_wmask(), .rvfi_mem_rdata(), .rvfi_mem_wdata()
    );
    /* verilator lint_on PINCONNECTEMPTY */
endmodule
